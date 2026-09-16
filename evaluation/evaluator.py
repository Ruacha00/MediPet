"""
亮点：端到端 Agent 评测框架

核心问题：如何评测端到端 Agent？

评测维度：
  1. 意图识别准确率 —— 预测意图 vs 标注意图，计算 Accuracy / F1
  2. 响应质量评分 —— 用 LLM 作为评判者（LLM-as-Judge），
     从相关性、准确性、完整性、有用性四个维度打分
  3. 端到端对话评测 —— 模拟完整多轮对话，评估整体体验
  4. 回归测试 —— 与历史基线对比，防止性能退化

LLM-as-Judge 是评测 Agent 质量的关键技术：
  人工标注成本高、主观性强；用 LLM 评判可以规模化、可重复。
"""
import asyncio
import json
import logging
import hashlib
import math
import pathlib
import statistics
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content, llm_request_options

from core.intent_recognizer import IntentRecognizer
from core.emergency import detect_emergency
from hospital.models import VisitIdentity, VisitMessage

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class IntentTestCase:
    message:          str
    expected_intent:  str
    context:          Optional[Dict[str, Any]] = None
    id:               Optional[str] = None
    patient_id:       Optional[str] = None
    expected_agent:   Optional[str] = None


@dataclass
class QualityScores:
    """LLM-as-Judge 评分结果。"""
    relevance:    float   # 相关性：回答是否针对问题
    accuracy:     float   # 准确性：信息是否正确
    completeness: float   # 完整性：是否完整解决问题
    helpfulness:  float   # 有用性：用户是否能据此行动
    judge_failed: bool = False
    error: Optional[str] = None

    @property
    def overall(self) -> float:
        return statistics.mean([self.relevance, self.accuracy, self.completeness, self.helpfulness])


@dataclass
class EvalResult:
    test_id:    str
    passed:     bool
    scores:     Dict[str, float]
    detail:     str = ""
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """评测报告。"""
    timestamp:        str
    total:            int
    passed:           int
    pass_rate:        float
    avg_scores:       Dict[str, float]
    regressions:      List[str]          # 相比基线退化的指标
    recommendations:  List[str]
    results:          List[EvalResult]
    run_id:           str = ""
    metadata:         Dict[str, Any] = field(default_factory=dict)
    judge_failures:   List[str] = field(default_factory=list)
    call_failures:    List[str] = field(default_factory=list)
    skipped:          List[str] = field(default_factory=list)
    candidate_path:   Optional[str] = None
    accepted_by:      Optional[str] = None


@dataclass
class EvaluationRuntime:
    """单个场景的依赖。工厂必须隔离存储、绑定工具，并负责退出时清理。

    case_runtime_factory(case, run_id) 返回异步上下文管理器，yield 本对象。
    时钟推进和故障开关仅供评测夹具使用，绝不转交模型执行。
    """
    orchestrator: Any
    memory_manager: Any = None
    visit_store: Any = None
    hospital_service: Any = None
    isolated: bool = False
    faults: tuple[str, ...] = ()
    advance_clock: Optional[Callable[[int], None]] = None
    model_calls: Optional[Callable[[], int]] = None


class ScenarioUnavailable(RuntimeError):
    """缺少显式隔离或故障夹具，场景跳过且不计通过。"""


# ── LLM-as-Judge ─────────────────────────────────────────────────────────────

class LLMJudge:
    """
    用 LLM 评判 Agent 响应质量。

    为什么用 LLM 而不是人工？
    - 可规模化：数千条测试用例自动评测
    - 可重复：相同输入得到稳定评分
    - 多维度：同时评估相关性、准确性等多个维度

    注意：LLM Judge 本身也有偏差，建议定期用人工标注校准。
    """

    JUDGE_PROMPT = """你是 MediPet 门诊就诊助手的质量评估专家。请对以下响应进行评分。

用户问题: {question}
Agent 响应: {response}
{context_section}

请从以下四个维度评分（0.0-1.0），返回 JSON：
- relevance: 响应是否直接针对用户问题（0=完全无关，1=完全相关）
- accuracy: 信息是否准确无误（0=明显错误，1=完全正确）
- completeness: 是否完整解决了用户需求（0=完全没解决，1=完全解决）
- helpfulness: 用户能否据此采取行动（0=毫无帮助，1=非常有帮助）

只返回 JSON，例如: {{"relevance": 0.9, "accuracy": 0.8, "completeness": 0.7, "helpfulness": 0.85}}"""

    def __init__(self, client: AsyncAnthropic, model: str):
        self._client = client
        self._model  = model

    async def judge(
        self,
        question: str,
        response: str,
        context: Optional[str] = None,
    ) -> QualityScores:
        ctx_section = f"背景信息: {context}" if context else ""
        prompt = self.JUDGE_PROMPT.format(
            question=question,
            response=response,
            context_section=ctx_section,
        )
        prompt = self._clean_text(prompt)
        try:
            resp = await self._client.messages.create(
                **llm_request_options(),
                model=self._model, max_tokens=256, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            keys = ("relevance", "accuracy", "completeness", "helpfulness")
            if any(isinstance(data[key], bool) for key in keys):
                raise ValueError("Judge 评分不能使用布尔值")
            values = [float(data[key]) for key in keys]
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
                raise ValueError("Judge 四维评分必须为 0 到 1 的有限数")
            return QualityScores(*values)
        except Exception as ex:
            logger.warning(f"LLM Judge 失败: {ex}")
            return QualityScores(
                0.0, 0.0, 0.0, 0.0,
                judge_failed=True,
                error=str(ex),
            )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ── 意图识别评测 ──────────────────────────────────────────────────────────────

class IntentEvaluator:
    """评测意图识别的准确率和 F1。"""

    def __init__(self, recognizer: IntentRecognizer):
        self._recognizer = recognizer

    async def evaluate(self, cases: List[IntentTestCase]) -> Dict[str, Any]:
        predictions, ground_truth = [], []
        case_details: List[Dict[str, Any]] = []

        for case in cases:
            detail = {"id": case.id, "message": case.message, "expected": case.expected_intent,
                      "patient_id": case.patient_id, "expected_agent": case.expected_agent, "context": case.context}
            try:
                result = await self._recognizer.recognize(case.message, history=(case.context or {}).get("history"))
                predicted = result.intent.value
                detail.update(predicted=predicted, confidence=result.confidence, reasoning=result.reasoning)
            except Exception as exc:
                predicted = "__call_failed__"
                detail.update(predicted=None, call_failed=True, error=str(exc))
            predictions.append(predicted)
            ground_truth.append(case.expected_intent)
            case_details.append(detail)

        # 纯 Python 计算指标
        correct = sum(p == g for p, g in zip(predictions, ground_truth))
        accuracy = correct / len(predictions) if predictions else 0.0

        # 每类 F1
        # 调用失败是缺失预测，计入 FN；它不是新增的业务意图类别。
        labels = sorted(set(ground_truth + [p for p in predictions if p != "__call_failed__"]))
        per_class: Dict[str, Dict[str, float]] = {}
        for label in labels:
            tp = sum(p == label and g == label for p, g in zip(predictions, ground_truth))
            fp = sum(p == label and g != label for p, g in zip(predictions, ground_truth))
            fn = sum(p != label and g == label for p, g in zip(predictions, ground_truth))
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec  = tp / (tp + fn) if (tp + fn) else 0.0
            f1   = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            per_class[label] = {"precision": prec, "recall": rec, "f1": f1}

        macro_f1 = statistics.mean(v["f1"] for v in per_class.values()) if per_class else 0.0

        return {
            "accuracy":   round(accuracy, 4),
            "macro_f1":   round(macro_f1, 4),
            "per_class":  per_class,
            "total":      len(cases),
            "correct":    correct,
            "cases":      case_details,
            "call_failures": sum(bool(case.get("call_failed")) for case in case_details),
        }


# ── 端到端评测器 ──────────────────────────────────────────────────────────────

class EndToEndEvaluator:
    """直接编排质量与有限医院场景评测；HTTP 协议验收由 I04 负责。"""

    PASS_THRESHOLD = 0.75
    QUALITY_DIMENSIONS = ("relevance", "accuracy", "completeness", "helpfulness")
    TRANSACTION_BOUNDARIES = {
        "duplicate-confirm", "superseded-confirm", "wrong-patient",
        "expired-proposal", "last-slot-race", "duplicate-cancel",
    }

    def __init__(self, orchestrator, recognizer: IntentRecognizer, api_key: str,
                 base_url: Optional[str] = None, model: str = "claude-3-5-sonnet-20241022",
                 baseline_path: Optional[str] = None, *, candidate_dir: Optional[str] = None,
                 case_runtime_factory=None, judge=None):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._orchestrator = orchestrator
        self._judge = judge if judge is not None else LLMJudge(AsyncAnthropic(**kwargs), model)
        self._model = model
        self._intent_evaluator = IntentEvaluator(recognizer)
        self._case_runtime_factory = case_runtime_factory
        self._history: List[EvalReport] = []
        self._baseline_path = pathlib.Path(baseline_path) if baseline_path else None
        self._candidate_dir = (pathlib.Path(candidate_dir) if candidate_dir else
                               self._baseline_path.parent / "candidates" if self._baseline_path else None)
        self._baseline = self._load_baseline()

    async def run(self, intent_cases: Optional[List[IntentTestCase]] = None,
                  dialog_cases: Optional[List[Dict[str, Any]]] = None,
                  boundary_cases: Optional[List[Dict[str, Any]]] = None,
                  *, metadata: Optional[Dict[str, Any]] = None) -> EvalReport:
        run_id = uuid4().hex
        results: List[EvalResult] = []
        intent_metrics = {}
        if intent_cases:
            intent_metrics = await self._intent_evaluator.evaluate(intent_cases)
            results.append(EvalResult(
                "intent_recognition", intent_metrics["accuracy"] >= self.PASS_THRESHOLD and not intent_metrics["call_failures"],
                {"accuracy": intent_metrics["accuracy"], "macro_f1": intent_metrics["macro_f1"]},
                f"准确率 {intent_metrics['accuracy']:.1%}，Macro-F1 {intent_metrics['macro_f1']:.3f}",
                {"kind": "intent", **intent_metrics},
            ))
        for kind, cases in (("dialog", dialog_cases), ("boundary", boundary_cases)):
            for index, case in enumerate(cases or []):
                results.extend(await self._evaluate_dialog_case(case, index, run_id=run_id, kind=kind))
        quality = [r for r in results if r.metadata.get("kind") == "quality" and not r.metadata.get("judge_failed")]
        avg_scores = {
            key: round(statistics.mean(r.scores[key] for r in quality if key in r.scores), 4)
            for key in self.QUALITY_DIMENSIONS if any(key in r.scores for r in quality)
        }
        if intent_metrics:
            avg_scores["intent_accuracy"] = intent_metrics["accuracy"]
        judges = [r.test_id for r in results if r.metadata.get("judge_failed")]
        calls = [r.test_id for r in results if r.metadata.get("call_failed")]
        calls.extend(f"intent:{c.get('id') or i}" for i, c in enumerate(intent_metrics.get("cases", [])) if c.get("call_failed"))
        skipped = [r.test_id for r in results if r.metadata.get("status") == "skipped"]
        business_failures = [r.test_id for r in results if r.metadata.get("kind") == "business" and not r.passed and r.test_id not in skipped]
        recommendations = self._recommendations(avg_scores, intent_metrics)
        if judges or calls or skipped or business_failures:
            recommendations = [r for r in recommendations if r != "所有已测指标均达标，继续保持"]
            recommendations.append(f"先核对失败与未执行样本：调用失败 {len(calls)}，Judge 失败 {len(judges)}，业务断言未通过 {len(business_failures)}，跳过 {len(skipped)}。")
            if business_failures:
                recommendations.append("业务断言未通过：" + "、".join(business_failures))
        passed = sum(result.passed for result in results)
        inputs = {"intent_cases": [asdict(c) for c in intent_cases or []], "dialog_cases": dialog_cases or [], "boundary_cases": boundary_cases or []}
        report = EvalReport(
            timestamp=datetime.now().astimezone().isoformat(), total=len(results), passed=passed,
            pass_rate=round(passed / len(results), 4) if results else 0.0,
            avg_scores=avg_scores, regressions=self._detect_regressions(avg_scores),
            recommendations=recommendations, results=results, run_id=run_id,
            metadata={**(metadata or {}), "model": self._model, "judge_parameters": {"temperature": 0.0, "max_tokens": 256},
                      "input_sha256": hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                      "case_counts": {"intent": len(intent_cases or []), "dialog": len(dialog_cases or []), "boundary": len(boundary_cases or [])},
                      "valid_quality_count": len(quality), "business_assertions": [r.test_id for r in results if r.metadata.get("kind") == "business"],
                      "business_failures": business_failures,
                      "baseline_status": "candidate", "scope": "direct_orchestrator_and_service; HTTP assertions belong to I04"},
            judge_failures=judges, call_failures=calls, skipped=skipped,
        )
        self._history.append(report)
        if self._candidate_dir:
            self._candidate_dir.mkdir(parents=True, exist_ok=True)
            target = self._candidate_dir / f"{run_id}.json"
            report.candidate_path = str(target)
            target.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @asynccontextmanager
    async def _case_runtime(self, case, run_id):
        if self._case_runtime_factory:
            async with self._case_runtime_factory(case, run_id) as runtime:
                if not runtime.isolated:
                    raise ScenarioUnavailable("场景依赖必须使用独立评测存储，不可复用在线业务命名空间")
                yield runtime
        else:
            yield EvaluationRuntime(orchestrator=self._orchestrator)

    async def _evaluate_dialog_case(self, case: Dict[str, Any], case_idx: int,
                                    *, run_id: str = "", kind: str = "dialog") -> List[EvalResult]:
        case_id = str(case.get("id") or f"{kind}_{case_idx}")
        state = {"case": case, "id": case_id, "turns": [], "history": [], "facts": {},
                 "actions": [], "snapshots": [], "recall_calls": 0}
        results = []
        try:
            async with self._case_runtime(case, run_id) as runtime:
                state["runtime"] = runtime
                business = bool(case.get("assertions") or case.get("setup") or case.get("between_turns") or case.get("after_turns") or kind == "boundary")
                if business and not (runtime.isolated and runtime.hospital_service and runtime.visit_store):
                    raise ScenarioUnavailable("业务场景需要 case_runtime_factory 提供隔离的医院服务与事项存储")
                user_id, patient_id = case.get("user_id") or "anonymous", case.get("patient_id") or "patient_self"
                if runtime.visit_store:
                    state["identity"] = await self._new_identity(runtime, user_id, patient_id)
                else:
                    state["identity"] = VisitIdentity(user_id=user_id, patient_id=patient_id, conv_id=f"eval-{uuid4().hex}")
                state["original_identity"] = state["identity"].model_dump(mode="json")
                state["model_calls_before"] = runtime.model_calls() if runtime.model_calls else None
                if business:
                    clock = runtime.hospital_service.now().isoformat()
                    state["clock"] = clock
                    if case.get("clock") and datetime.fromisoformat(case["clock"]) != runtime.hospital_service.now():
                        raise ScenarioUnavailable(f"场景时钟不匹配：要求 {case['clock']}，实际 {clock}")
                    state["initial"] = await self._snapshot(state)
                if case_id == "retrieval-failure" and "knowledge_backend_unavailable" not in runtime.faults:
                    raise ScenarioUnavailable("检索故障场景尚未注入 knowledge_backend_unavailable")
                if case_id == "emergency-before-model" and ("model_unavailable" not in runtime.faults or not runtime.model_calls):
                    raise ScenarioUnavailable("急症故障场景需要 model_unavailable 与实际模型调用计数")
                for action in case.get("setup", []):
                    await self._action(action, state)
                if business:
                    state["before"] = await self._snapshot(state)
                if kind == "boundary" and case_id in self.TRANSACTION_BOUNDARIES:
                    await self._transaction_boundary(state)
                else:
                    questions = self._dialog_turns(case)
                    if not questions:
                        raise ScenarioUnavailable("未定义可执行聊天或已知确定性业务边界")
                    for turn_index, question in enumerate(questions):
                        for action in case.get("between_turns", {}).get(str(turn_index), []):
                            await self._action(action, state)
                        result = await self._turn(question, turn_index, state, judge=(kind == "dialog"))
                        if result is not None:
                            results.append(result)
                        if business:
                            state["snapshots"].append(await self._snapshot(state))
                    for action in case.get("after_turns", []):
                        await self._action(action, state)
                if business:
                    state["after"] = await self._snapshot(state)
                    facts = await self._business_facts(state)
                    assertions = self._check_assertions(case.get("assertions", []), facts, state)
                    passed = bool(assertions) and all(item["status"] == "passed" for item in assertions)
                    results.append(EvalResult(
                        f"{case_id}:business", passed, {}, "业务状态断言（独立于 Judge）",
                        {"kind": "business", "status": "passed" if passed else "failed", "assertions": assertions,
                         "facts": facts, "identity": state["identity"].model_dump(mode="json"), "clock": state["clock"],
                         "actions": state["actions"], "turns": state["turns"]},
                    ))
        except ScenarioUnavailable as exc:
            results.append(EvalResult(f"{case_id}:scenario", False, {}, str(exc),
                                      {"kind": "business", "status": "skipped", "actions": state["actions"], "turns": state["turns"]}))
        except Exception as exc:
            results.append(EvalResult(f"{case_id}:execution", False, {}, str(exc),
                                      {"kind": kind, "status": "failed", "call_failed": True,
                                       "error": str(exc), "actions": state["actions"], "turns": state["turns"]}))
        return results

    @staticmethod
    async def _new_identity(runtime, user_id, patient_id):
        visit = await runtime.visit_store.create_visit(user_id, patient_id, title="评测事项")
        return VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)

    async def _turn(self, question, turn_index, state, *, judge):
        from agents.agent_orchestrator import Request
        from memory.conversation_memory import MsgRole
        runtime, identity = state["runtime"], state["identity"]
        emergency = bool(detect_emergency(question))
        history = state["history"]
        context = self._history_context(history)
        if emergency:
            context, history = "", []
        elif runtime.memory_manager:
            state["recall_calls"] += 1
            memory = await runtime.memory_manager.get_context(identity, query=question)
            context = memory.to_prompt_text()
            history = [{"role": item.role.value, "content": item.content} for item in memory.recent_messages]
        response = await runtime.orchestrator.run(Request(
            message=question, **identity.model_dump(), context=context, history=history[-6:] or None,
        ))
        metadata = {"question": question, "response": response.response, "turn": turn_index,
                    **identity.model_dump(mode="json"), "emergency": emergency,
                    "agent_type": self._value(response.agent_type), "intent": self._value(response.intent),
                    "agent_types": [self._value(value) for value in response.agent_types],
                    "primary_agent": self._value(response.primary_agent),
                    "supporting_agents": [self._value(value) for value in response.supporting_agents],
                    "tools_used": list(response.tools_used), "tool_traces": list(response.tool_traces),
                    "artifacts": [artifact.model_dump(mode="json") for artifact in response.artifacts],
                    "latency_ms": response.latency_ms, "routing_reason": response.routing_reason,
                    "routing_confidence": response.routing_confidence}
        state["turns"].append(metadata)
        messages = [{"role": "user", "content": question}, {"role": "assistant", "content": response.response}]
        if runtime.visit_store:
            now = runtime.hospital_service.now() if runtime.hospital_service else datetime.now().astimezone()
            full = [VisitMessage(**identity.model_dump(), message_id=f"eval-{uuid4().hex}", created_at=now,
                                 **message, artifacts=response.artifacts if message["role"] == "assistant" else [],
                                 metadata={"evaluation": True, "emergency": emergency}) for message in messages]
            await runtime.visit_store.append_messages(identity.user_id, identity.conv_id, full)
        if runtime.memory_manager:
            for message in messages:
                await runtime.memory_manager.add_message(identity, MsgRole(message["role"]), message["content"],
                                                         {"evaluation": True, "emergency": emergency}, compress=not emergency)
        state["history"].extend(messages)
        failures = [trace for trace in response.tool_traces if trace.get("success") is False and
                    (trace.get("kind") in {"agent_execution", "composition"} or trace.get("call_success") is False)]
        if failures:
            return EvalResult(f"{state['id']}:turn:{turn_index}", False, {}, "编排记录了调用失败，质量分数未计入",
                              {**metadata, "kind": "execution", "status": "failed", "call_failed": True,
                               "execution_failures": failures})
        if not judge:
            return None
        # Judge 只消费实际发生的本轮证据，不读取场景断言、未来动作或预期分数。
        judge_context = json.dumps({
            "prior_context": context or None,
            "current_turn": {
                "identity": identity.model_dump(mode="json"),
                "business_clock": runtime.hospital_service.now().isoformat() if runtime.hospital_service else None,
                "artifacts": metadata["artifacts"],
                "tools_used": metadata["tools_used"],
                "tool_traces": metadata["tool_traces"],
            },
        }, ensure_ascii=False, indent=2)
        metadata["judge_context"] = judge_context
        try:
            quality = await self._judge.judge(question, response.response, context=judge_context)
            if not quality.judge_failed and not all(math.isfinite(getattr(quality, key)) and 0 <= getattr(quality, key) <= 1 for key in self.QUALITY_DIMENSIONS):
                raise ValueError("Judge 返回无效四维评分")
        except Exception as exc:
            quality = QualityScores(0, 0, 0, 0, judge_failed=True, error=str(exc))
        scores = {} if quality.judge_failed else {key: getattr(quality, key) for key in self.QUALITY_DIMENSIONS}
        if scores:
            scores["overall"] = quality.overall
        passed = not quality.judge_failed and quality.overall >= self.PASS_THRESHOLD
        return EvalResult(f"{state['id']}:turn:{turn_index}", passed, scores,
                          f"Judge 失败：{quality.error}" if quality.judge_failed else f"综合评分 {quality.overall:.3f}",
                          {**metadata, "kind": "quality", "status": "passed" if passed else "failed",
                           "judge_failed": quality.judge_failed, "judge_error": quality.error})

    @staticmethod
    def _value(value):
        return getattr(value, "value", value)

    @staticmethod
    def _require_success(result):
        if not result.success:
            raise RuntimeError(f"业务调用失败：{result.error_code}: {result.error}")
        return result.data

    async def _snapshot(self, state):
        runtime, identity = state["runtime"], state["identity"]
        service = runtime.hospital_service
        appointments = self._require_success(await service.get_appointments(identity))["items"]
        slots_result = await service.search_slots()
        if not slots_result.success and slots_result.error_code != "no_slots":
            self._require_success(slots_result)
        slots = (slots_result.data or {}).get("slots", [])
        selection = await runtime.visit_store.get_selection(identity.user_id, identity.conv_id)
        proposal = await service.store.get_proposal(selection.current_proposal_id) if selection.current_proposal_id else None
        return {"appointments": appointments, "remaining": sum(slot["remaining"] for slot in slots),
                "selection": selection.model_dump(mode="json"),
                "proposal": proposal.model_dump(mode="json") if proposal else None}

    async def _prepare_first(self, state, *, identity=None, department="儿科", date="明天"):
        service = state["runtime"].hospital_service
        slots = self._require_success(await service.search_slots(department=department, date=date))["slots"]
        proposal = self._require_success(await service.prepare_appointment(identity or state["identity"], slot_id=slots[0]["slot_id"]))
        return proposal, slots

    async def _action(self, action, state):
        runtime, identity = state["runtime"], state["identity"]
        service, visits = runtime.hospital_service, runtime.visit_store
        name = action.get("action")
        event = {"action": name, "identity": identity.model_dump(mode="json")}
        if name == "create_existing_appointment":
            proposal, _ = await self._prepare_first(state, department=action.get("department", "儿科"), date=action.get("date", "明天"))
            event["receipt"] = self._require_success(await service.confirm_proposal(identity, proposal["proposal_id"]))
        elif name == "confirm_current":
            state["before_confirmation"] = await self._snapshot(state)
            proposal = self._require_success(await service.get_current_proposal(identity))
            result = await service.confirm_proposal(identity, proposal["proposal_id"])
            event["receipt"] = self._require_success(result)
            receipt = event["receipt"]
            common = {**identity.model_dump(), "created_at": receipt["executed_at"],
                      "proposal_id": receipt["proposal_id"], "receipt_id": receipt["receipt_id"]}
            await visits.append_messages(identity.user_id, identity.conv_id, [
                VisitMessage(**common, message_id=f"confirm:{receipt['receipt_id']}", role="user", kind="confirmation_event",
                             content="已在评测界面明确确认资料。"),
                VisitMessage(**common, message_id=f"result:{receipt['receipt_id']}", role="assistant", kind="operation_result",
                             content="预约操作已完成。", artifacts=result.artifacts, metadata={"receipt": receipt}),
            ])
            if runtime.memory_manager:
                await runtime.memory_manager.invalidate_working_memory(identity)
            state["after_confirmation"] = await self._snapshot(state)
        elif name == "switch_patient":
            state["identity"] = await self._new_identity(runtime, identity.user_id, action["patient_id"])
            state["history"] = []
            event["new_identity"] = state["identity"].model_dump(mode="json")
        elif name == "expire_working_memory":
            memory = runtime.memory_manager
            if not memory:
                raise ScenarioUnavailable("工作记忆恢复场景需要 MemoryManager")
            before = await visits.get_messages(identity.user_id, identity.conv_id)
            state["saved_selection"] = (await visits.get_selection(identity.user_id, identity.conv_id)).model_dump(mode="json")
            # 仅清除本隔离场景的工作窗口；完整历史和选择快照仍保留。
            await memory.invalidate_working_memory(identity)
            after = await visits.get_messages(identity.user_id, identity.conv_id)
            state["facts"]["complete_history.count.unchanged"] = len(before) == len(after)
            event.update(before_count=len(before), after_count=len(after))
        elif name in {"archive_visit", "restore_visit"}:
            before = await visits.get_messages(identity.user_id, identity.conv_id)
            visit = await visits.update_visit(identity.user_id, identity.conv_id, archived=name == "archive_visit")
            after = await visits.get_messages(identity.user_id, identity.conv_id)
            if name == "archive_visit":
                state["facts"]["archive.messages.unchanged"] = before == after
            else:
                state["facts"].update({"restored.patient_id": visit.patient_id, "restored.archived": visit.archived})
            event["visit"] = visit.model_dump(mode="json")
        else:
            raise ScenarioUnavailable(f"未支持的显式用户动作：{name}")
        state["actions"].append(event)

    async def _transaction_boundary(self, state):
        case_id, runtime, identity = state["id"], state["runtime"], state["identity"]
        service, facts = runtime.hospital_service, state["facts"]
        proposal, slots = await self._prepare_first(state)
        if case_id == "duplicate-confirm":
            first = self._require_success(await service.confirm_proposal(identity, proposal["proposal_id"]))
            second = self._require_success(await service.confirm_proposal(identity, proposal["proposal_id"]))
            facts["confirm_twice.receipt_id.equal"] = first["receipt_id"] == second["receipt_id"]
        elif case_id == "superseded-confirm":
            self._require_success(await service.prepare_appointment(identity, slot_id=slots[1]["slot_id"]))
            facts["old_proposal.confirm.error_code"] = (await service.confirm_proposal(identity, proposal["proposal_id"])).error_code
        elif case_id == "wrong-patient":
            other_patient = "patient_child" if identity.patient_id == "patient_self" else "patient_self"
            other = await self._new_identity(runtime, identity.user_id, other_patient)
            facts["foreign_proposal.confirm.error_code"] = (await service.confirm_proposal(other, proposal["proposal_id"])).error_code
        elif case_id == "expired-proposal":
            if runtime.advance_clock is None:
                raise ScenarioUnavailable("到期场景需要可推进的隔离业务时钟")
            start = service.now()
            runtime.advance_clock(900)
            facts["clock.advance_seconds"] = int((service.now() - start).total_seconds())
            facts["confirm.error_code"] = (await service.confirm_proposal(identity, proposal["proposal_id"])).error_code
        elif case_id == "last-slot-race":
            # 工厂已确认隔离，只有此临时场景的号源被设置为余一号。
            slot = await service.store.get_slot(slots[0]["slot_id"])
            await service.store.redis.set(service.store.slot_key(slot.slot_id), slot.model_copy(update={"remaining": 1}).model_dump_json())
            other = await self._new_identity(runtime, identity.user_id, identity.patient_id)
            second = self._require_success(await service.prepare_appointment(other, slot_id=slot.slot_id))
            facts["initial.remaining"] = (await service.store.get_slot(slot.slot_id)).remaining
            results = await asyncio.gather(service.confirm_proposal(identity, proposal["proposal_id"]),
                                           service.confirm_proposal(other, second["proposal_id"]))
            facts["parallel_confirm.executions"] = sum(result.success for result in results)
            facts["final.remaining"] = (await service.store.get_slot(slot.slot_id)).remaining
            facts["parallel_confirm.results"] = [result.model_dump(mode="json") for result in results]
        elif case_id == "duplicate-cancel":
            created = self._require_success(await service.confirm_proposal(identity, proposal["proposal_id"]))
            cancel = self._require_success(await service.prepare_cancellation(identity, created["appointment"]["appointment_id"]))
            first = self._require_success(await service.confirm_proposal(identity, cancel["proposal_id"]))
            second = self._require_success(await service.confirm_proposal(identity, cancel["proposal_id"]))
            facts["cancel_twice.receipt_id.equal"] = first["receipt_id"] == second["receipt_id"]
            facts["final.remaining"] = (await service.store.get_slot(slots[0]["slot_id"])).remaining
            facts["initial.capacity"] = slots[0]["capacity"]
        state["actions"].append({"action": case_id, "proposal_id": proposal["proposal_id"], "facts": dict(facts)})

    async def _business_facts(self, state):
        runtime, identity = state["runtime"], state["identity"]
        after, before = state["after"], state["before"]
        selection, proposal = after["selection"], after["proposal"] or {}
        turns = state["turns"]
        artifacts = [artifact for turn in turns for artifact in turn["artifacts"]]
        traces = [trace for turn in turns for trace in turn["tool_traces"]]
        knowledge = [trace for trace in traces if trace.get("tool_name") == "search_knowledge_base"]
        # 检索条目数不证明来源存在，只统计本次成功 trace 返回的来源标识。
        source_count = sum(
            1 for trace in knowledge
            if trace.get("success") is True and isinstance(trace.get("sources"), list)
            for source in trace["sources"]
            if isinstance(source, dict) and any(
                isinstance(source.get(key), str) and source[key].strip()
                for key in ("source", "source_id", "doc_id", "chunk_id")
            )
        )
        # 协作要求同一复合轮同时完成两个职责，不能拼接不同轮的角色和卡片。
        role_turns = turns[:1] if state["id"] == "collaboration" else turns
        role_artifacts = [artifact for turn in role_turns for artifact in turn["artifacts"]]
        agents = sorted({agent for turn in role_turns for agent in turn["agent_types"]})
        facts = {**state["facts"], "appointments.count": len(after["appointments"]),
                 "slot_delta": after["remaining"] - before["remaining"],
                 "proposal.status": proposal.get("status"), "proposal.target_id": proposal.get("target_id"),
                 "current_proposal.target_id": proposal.get("target_id"),
                 "query.department_id": selection["query"].get("department_id"),
                 "query.date": selection["query"].get("date"), "query.period": selection["query"].get("period"),
                 "selection.status": selection["status"], "selection.slots.count": len(selection["slots"]),
                 "agents.include": agents, "has_artifact": sorted({a["type"] for a in role_artifacts}),
                 "has_artifacts": sorted({a["type"] for a in role_artifacts}),
                 "knowledge_sources.nonempty": source_count > 0, "knowledge.sources.count": source_count,
                 "trace.retrieval_failed": any(trace.get("success") is False for trace in knowledge),
                 "tool.error_code": [trace.get("error_code") for trace in traces if trace.get("error_code")],
                 "memory.recall_calls": state["recall_calls"],
                 "emergency.interrupted": bool(turns) and turns[-1]["intent"] == "emergency" and turns[-1]["agent_type"] == "escalation"}
        if "knowledge_backend_unavailable" in runtime.faults:
            facts["fault"] = "knowledge_backend_unavailable"
        if runtime.model_calls and state["model_calls_before"] is not None:
            facts["model.calls"] = runtime.model_calls() - state["model_calls_before"]
        if identity.patient_id == "patient_self":
            facts.update({"self.appointments.count": len(after["appointments"]), "self.selection.slots.count": len(selection["slots"])})
        if state["id"] == "patient-switch":
            source = state["snapshots"][0] if state["snapshots"] else {}
            facts["source.selection.nonempty"] = bool(source.get("selection", {}).get("slots"))
            facts["source.proposal.status"] = (source.get("proposal") or {}).get("status")
        for artifact in reversed(artifacts):
            if artifact["type"] == "wayfinding":
                facts.update({"wayfinding.destination_id": artifact["data"].get("destination_id"), "wayfinding.mode": artifact["data"].get("mode")})
                break
        for prefix in ("before_confirmation", "after_confirmation"):
            if prefix in state:
                snapshot = state[prefix]
                facts[f"{prefix}.appointments.count"] = len(snapshot["appointments"])
                if snapshot["appointments"]:
                    facts[f"{prefix}.appointment.status"] = snapshot["appointments"][0]["status"]
        if "after_confirmation" in state:
            facts["after_confirmation.slot_delta"] = state["after_confirmation"]["remaining"] - state["before_confirmation"]["remaining"]
        if len(state["snapshots"]) > 1 and state["snapshots"][0]["proposal"]:
            old = await runtime.hospital_service.store.get_proposal(state["snapshots"][0]["proposal"]["proposal_id"])
            facts["old_proposal.status"] = old.status if old else None
        return facts

    @staticmethod
    def _check_assertions(assertions, facts, state):
        """只比较已采集事实；不执行断言文本、Python 表达式或模型判断。"""
        expected_refs = {"initial.capacity": facts.get("initial.capacity")}
        for label, selection in (("last_slot_list", state["after"]["selection"]), ("saved_slot_list", state.get("saved_selection", {}))):
            for index, slot in enumerate(selection.get("slots", [])):
                expected_refs[f"{label}.slots[{index}].slot_id"] = slot["slot_id"]
        assertions = list(assertions)
        if state["id"] == "patient-switch":
            assertions += ["source.selection.nonempty=true", "source.proposal.status=pending"]
        checks = []
        for expression in assertions:
            key, separator, expected_text = expression.partition("=")
            actual = facts.get(key)
            expected = expected_refs.get(expected_text, expected_text)
            if expected_text in {"true", "false"}:
                expected = expected_text == "true"
            elif expected_text.lstrip("-").isdigit():
                expected = int(expected_text)
            supported = bool(separator) and key in facts and expected is not None
            if key in {"agents.include", "has_artifacts", "has_artifact", "tool.error_code"} and supported:
                passed = set(expected_text.split(",")).issubset(actual)
            else:
                passed = supported and actual == expected
            checks.append({"assertion": expression, "actual": actual, "expected": expected,
                           "status": "passed" if passed else "failed" if supported else "skipped"})
        return checks

    @staticmethod
    def _dialog_turns(case: Dict[str, Any]) -> List[str]:
        turns = case.get("turns")
        if turns is not None:
            if not isinstance(turns, list) or any(not isinstance(turn, str) or not turn.strip() for turn in turns):
                raise ValueError("turns 必须只包含非空用户聊天文字；界面动作请使用显式动作字段")
            return turns
        question = case.get("question")
        return [question] if isinstance(question, str) and question.strip() else []

    @staticmethod
    def _history_context(history: List[Dict[str, str]]) -> str:
        return "[评测多轮历史]\n" + "\n".join(f"{m['role']}: {m['content']}" for m in history[-8:]) if history else ""

    def _detect_regressions(self, current: Dict[str, float]) -> List[str]:
        """沿用相对下降超过 5% 的比较算法，仅以人工接受报告为参考。"""
        if self._baseline is None:
            return []
        prev, regressions = self._baseline.avg_scores, []
        for metric, value in current.items():
            if metric in prev and prev[metric] > 0:
                delta = (value - prev[metric]) / prev[metric]
                if delta < -0.05:
                    regressions.append(f"{metric}: {prev[metric]:.3f} → {value:.3f} (退化 {abs(delta):.1%})")
        return regressions

    def _recommendations(self, scores: Dict[str, float], intent_metrics: Dict[str, Any]) -> List[str]:
        recs = []
        if scores.get("intent_accuracy", 1.0) < 0.90:
            recs.append("意图识别准确率 < 90%：增加 Few-shot 示例，或对低 F1 的意图类别补充训练数据")
        if scores.get("relevance", 1.0) < 0.75:
            recs.append("相关性偏低：检查 Agent system_prompt，确保 Agent 聚焦于用户问题")
        if scores.get("completeness", 1.0) < 0.75:
            recs.append("完整性偏低：Agent 可能过早结束回答，考虑在 prompt 中要求提供完整解决方案")
        if scores.get("helpfulness", 1.0) < 0.75:
            recs.append("有用性偏低：回答可能过于抽象，考虑要求 Agent 提供具体操作步骤")
        return recs or ["所有已测指标均达标，继续保持" if scores else "本次没有有效质量或意图分数，查看业务断言与未执行原因。"]

    @property
    def history(self) -> List[EvalReport]:
        return self._history

    def accept_baseline(self, report: EvalReport, *, reviewed_by: str) -> None:
        """仅由人工复核后的显式调用接受；run 永不调用此入口。"""
        if not reviewed_by or not reviewed_by.strip():
            raise ValueError("接受基线必须填写人工复核人")
        if not self._baseline_path:
            raise ValueError("未配置接受基线路径")
        accepted = self._report_from_dict(asdict(report))
        accepted.accepted_by = reviewed_by.strip()
        accepted.metadata.update(baseline_status="accepted", accepted_at=datetime.now().astimezone().isoformat())
        self._save_baseline(accepted)

    def _load_baseline(self) -> Optional[EvalReport]:
        if not self._baseline_path or not self._baseline_path.exists():
            return None
        try:
            report = self._report_from_dict(json.loads(self._baseline_path.read_text(encoding="utf-8")))
            # 旧版自动保存报告不能冒充已经人工接受的正式基线。
            return report if report.accepted_by and report.metadata.get("baseline_status") == "accepted" else None
        except Exception as exc:
            logger.warning("读取评测基线失败: %s", exc)
            return None

    def _save_baseline(self, report: EvalReport) -> None:
        if not self._baseline_path or not report.accepted_by or report.metadata.get("baseline_status") != "accepted":
            raise ValueError("只允许保存经过显式人工复核的基线")
        self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self._baseline_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        self._baseline = report

    @staticmethod
    def _report_from_dict(data: Dict[str, Any]) -> EvalReport:
        return EvalReport(
            timestamp=data.get("timestamp", ""), total=int(data.get("total", 0)), passed=int(data.get("passed", 0)),
            pass_rate=float(data.get("pass_rate", 0.0)), avg_scores=dict(data.get("avg_scores", {})),
            regressions=list(data.get("regressions", [])), recommendations=list(data.get("recommendations", [])),
            results=[EvalResult(test_id=r.get("test_id", ""), passed=bool(r.get("passed", False)),
                                scores=dict(r.get("scores", {})), detail=r.get("detail", ""), metadata=dict(r.get("metadata", {})))
                     for r in data.get("results", [])],
            run_id=data.get("run_id", ""), metadata=dict(data.get("metadata", {})),
            judge_failures=list(data.get("judge_failures", [])), call_failures=list(data.get("call_failures", [])),
            skipped=list(data.get("skipped", [])), candidate_path=data.get("candidate_path"), accepted_by=data.get("accepted_by"),
        )


def _load_cases(name):
    document = json.loads((pathlib.Path(__file__).with_name("cases") / f"{name}.json").read_text(encoding="utf-8"))
    return [{"clock": document["clock"], "user_id": document["user_id"], **case} for case in document["cases"]]


DEFAULT_INTENT_CASES = [IntentTestCase(
    message=case["message"], expected_intent=case["expected_intent"], id=case["id"],
    patient_id=case.get("patient_id"), expected_agent=case.get("expected_agent"),
    context={"clock": case["clock"], "user_id": case["user_id"]},
) for case in _load_cases("intents")]
DEFAULT_DIALOG_CASES = _load_cases("dialogs")
DEFAULT_BOUNDARY_CASES = _load_cases("boundaries")

