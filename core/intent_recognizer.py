"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content, llm_request_options
from core.emergency import EMERGENCY_SIGNALS, detect_emergency, is_emergency_reference
from hospital.models import BUSINESS_TIMEZONE

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    QUERY      = "query"       # 查询信息
    COMPLAINT  = "complaint"   # 投诉不满
    REQUEST    = "request"     # 请求操作
    GREETING   = "greeting"    # 问候
    ESCALATION = "escalation"  # 要求升级/转人工
    FEEDBACK   = "feedback"    # 正面反馈
    GUIDANCE   = "guidance"    # 就诊指引兜底
    APPOINTMENT = "appointment"  # 预约事务兜底
    HOSPITAL_INFO = "hospital_info"
    DEPARTMENT_INFO = "department_info"
    DOCTOR_INFO = "doctor_info"
    SLOT_QUERY = "slot_query"
    APPOINTMENT_CREATE = "appointment_create"
    APPOINTMENT_STATUS = "appointment_status"
    APPOINTMENT_CANCEL = "appointment_cancel"
    VISIT_PREPARATION = "visit_preparation"
    VISIT_PROCESS = "visit_process"
    WAYFINDING = "wayfinding"
    HUMAN_HANDOFF = "human_handoff"
    SYMPTOM_QUERY = "symptom_query"
    MEDICATION_QUERY = "medication_query"
    REPORT_QUERY = "report_query"
    EMERGENCY = "emergency"
    OTHER      = "other"

class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent:     IntentCategory
    confidence: float
    urgency:    UrgencyLevel
    intent_group: str
    entities:   Dict[str, List[str]]   # 从消息中提取的实体
    reasoning:  str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
_TEMPLATES: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["我想了解一些信息", "这是什么意思？", "能介绍一下吗？"],
    IntentCategory.COMPLAINT:  ["等了好几个小时！", "服务太差了！", "一直没人处理！"],
    IntentCategory.REQUEST:    ["请帮我处理一下", "我需要帮助", "请协助办理"],
    IntentCategory.GREETING:   ["你好", "嗨，有人吗", "早上好"],
    IntentCategory.ESCALATION: ["请升级处理这个问题", "需要进一步协助", "这个问题还没解决"],
    IntentCategory.FEEDBACK:   ["服务很棒！", "非常满意", "给个好评"],
    IntentCategory.GUIDANCE: ["我需要就诊指引", "请说明就医安排", "给我一些到院指引"],
    IntentCategory.APPOINTMENT: ["我想咨询预约事务", "处理一下挂号相关事项", "预约方面需要帮助"],
    IntentCategory.HOSPITAL_INFO: ["医院几点开始门诊？", "医院地址是什么？", "介绍一下医院"],
    IntentCategory.DEPARTMENT_INFO: ["儿科的介绍是什么？", "医院有哪些科室？", "想了解科室说明"],
    IntentCategory.DOCTOR_INFO: ["明天有哪些儿科医生出诊？", "介绍一下许知宁医生", "查看医生排班"],
    IntentCategory.SLOT_QUERY: ["明天儿科还有号吗？", "查询后天下午的号源", "看看眼科可选的号"],
    IntentCategory.APPOINTMENT_CREATE: ["选择这个号，帮我预约。", "就选第一个号源", "为这个号准备预约资料"],
    IntentCategory.APPOINTMENT_STATUS: ["看一下孩子的预约记录。", "查看我的预约", "这条预约是什么状态？"],
    IntentCategory.APPOINTMENT_CANCEL: ["取消这条预约。", "我想取消预约", "准备取消这条记录"],
    IntentCategory.VISIT_PREPARATION: ["第一次就诊需要带什么？", "查看儿科就诊材料清单", "到院前需要准备什么？"],
    IntentCategory.VISIT_PROCESS: ["到院后先在哪里报到？", "说明门诊就诊流程", "报到后怎么办理？"],
    IntentCategory.WAYFINDING: ["从门诊大厅怎么去药房？", "从收费处到检验科怎么走？", "有去儿科诊区的无障碍路线吗？"],
    IntentCategory.HUMAN_HANDOFF: ["我想联系人工导诊。", "导诊联系电话是什么？", "请帮我找人工"],
    IntentCategory.SYMPTOM_QUERY: ["我咳嗽流鼻涕，该挂哪个科？", "孩子肚子不舒服，需要看哪个科室？", "眼睛发红发痒应该去哪里看？"],
    IntentCategory.MEDICATION_QUERY: ["布洛芬200mg普通片的说明书怎么服用？", "对乙酰氨基酚有哪些禁忌？", "布洛芬和华法林有没有相互作用？"],
    IntentCategory.REPORT_QUERY: ["帮我整理检查报告里的项目和参考区间", "血红蛋白 120 g/L 参考范围 115-150，请整理", "可以上传化验单图片吗？"],
    IntentCategory.EMERGENCY: ["现在呼吸困难", "有人突然失去意识", "现在剧烈胸痛"],
}

_ACTIVE_INTENTS = frozenset(_TEMPLATES) | {IntentCategory.OTHER}

_SPECIFIC_INTENTS = {
    IntentCategory.HOSPITAL_INFO,
    IntentCategory.DEPARTMENT_INFO,
    IntentCategory.DOCTOR_INFO,
    IntentCategory.SLOT_QUERY,
    IntentCategory.APPOINTMENT_CREATE,
    IntentCategory.APPOINTMENT_STATUS,
    IntentCategory.APPOINTMENT_CANCEL,
    IntentCategory.VISIT_PREPARATION,
    IntentCategory.VISIT_PROCESS,
    IntentCategory.WAYFINDING,
    IntentCategory.HUMAN_HANDOFF,
    IntentCategory.SYMPTOM_QUERY,
    IntentCategory.MEDICATION_QUERY,
    IntentCategory.REPORT_QUERY,
    IntentCategory.EMERGENCY,
}

_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.APPOINTMENT,
    IntentCategory.GUIDANCE,
    IntentCategory.ESCALATION,
}

_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.HOSPITAL_INFO: IntentCategory.QUERY,
    IntentCategory.DEPARTMENT_INFO: IntentCategory.QUERY,
    IntentCategory.DOCTOR_INFO: IntentCategory.QUERY,
    IntentCategory.SLOT_QUERY: IntentCategory.APPOINTMENT,
    IntentCategory.APPOINTMENT_CREATE: IntentCategory.APPOINTMENT,
    IntentCategory.APPOINTMENT_STATUS: IntentCategory.APPOINTMENT,
    IntentCategory.APPOINTMENT_CANCEL: IntentCategory.APPOINTMENT,
    IntentCategory.VISIT_PREPARATION: IntentCategory.GUIDANCE,
    IntentCategory.VISIT_PROCESS: IntentCategory.GUIDANCE,
    IntentCategory.WAYFINDING: IntentCategory.GUIDANCE,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
    IntentCategory.SYMPTOM_QUERY: IntentCategory.QUERY,
    IntentCategory.MEDICATION_QUERY: IntentCategory.QUERY,
    IntentCategory.REPORT_QUERY: IntentCategory.QUERY,
    IntentCategory.EMERGENCY: IntentCategory.ESCALATION,
}

# 紧急关键词
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],
}


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    LLM 通过 Anthropic 兼容接口调用；默认向量是本地字符 n-gram 哈希。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self.model     = model
        self.threshold = confidence_threshold
        self._timezone = ZoneInfo(BUSINESS_TIMEZONE)
        self._clock = clock or (lambda: datetime.now(self._timezone))
        # 本地字符 n-gram 向量始终可用；如果未来客户端暴露 embeddings 资源，
        # _embed_text 会优先尝试远端向量，否则自动回退本地向量。
        self._embedding_enabled = True

        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        self._cache: Dict[str, IntentResult] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """
        识别用户意图。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        """
        today = self._today()
        key = self._cache_key(message, history, today=today)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        t0 = time.monotonic()

        # LLM 和 Embedding 并行（Embedding 不可用时跳过）
        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        pat      = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        intent, confidence, source_scores = self._vote(llm, emb, pat)
        # 三路投票保持原机制；模型不能将否定或知识询问升级为固定急症。
        if intent == IntentCategory.EMERGENCY and not detect_emergency(message):
            intent = IntentCategory.QUERY
        entities = self._extract_entities(message, today=today)
        urgency  = self._urgency(message, intent)

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
        )

        # LRU 缓存
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
        self._cache[key] = result
        return result

    def learn(self, message: str, correct: IntentCategory) -> None:
        """在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。"""
        if correct not in _ACTIVE_INTENTS:
            raise ValueError("不支持的 MediPet 意图")
        tpls = _TEMPLATES.setdefault(correct, [])
        if message not in tpls:
            tpls.append(message)
            self._tpl_embeddings.pop(correct, None)  # 下次重新计算
            self._cache.clear()  # 模板更新后旧缓存可能对应过时结果
            logger.info(f"学习新样本 → {correct.value}: {message[:40]}")

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """策略 1：LLM 语义理解（Few-shot + 上下文）。"""
        message = self._clean_text(message)
        # 构建 Few-shot 示例
        examples = "\n".join(
            f'  消息: "{t}" → 意图: {cat.value}'
            for cat, tpls in _TEMPLATES.items()
            for t in tpls[:1]  # 每类取 1 条，控制 prompt 长度
        )
        # 最近 3 轮对话上下文
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                for m in history[-3:]
            )

        prompt = f"""你是 MediPet 门诊就诊助手的意图分析器。根据示例判断用户意图，返回 JSON。
如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
例如查号源优先返回 slot_query，就诊材料优先返回 visit_preparation，取消预约优先返回 appointment_cancel。
按症状询问科室或描述身体不适返回 symptom_query；药物说明书、剂量信息、禁忌和相互作用返回 medication_query；报告数值、参考范围、化验单整理或报告上传返回 report_query。
取药地点/流程属于就诊指引；药房不等于用药咨询，材料里的既往报告不等于报告预处理。
结合最近对话理解“明天呢”“第一个”“还是下午吧”；不猜测号源 ID，不改变当前患者身份。
一般急症知识咨询和明确否定不是当前急症；本分类器不提供诊断或用药建议。

示例:
{examples}

        {ctx}
        用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory if c in _ACTIVE_INTENTS)}"""
        prompt = self._clean_text(prompt)

        try:
            resp = await self.client.messages.create(
                **llm_request_options(),
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
                if data["intent"] not in _ACTIVE_INTENTS:
                    data["intent"] = IntentCategory.OTHER
            except ValueError:
                data["intent"] = IntentCategory.OTHER
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "reasoning": "LLM 失败", "failed": True}

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配。"""
        try:
            await self._load_template_embeddings()
            msg_vec = await self._embed_text(message)

            best_cat, best_score = IntentCategory.OTHER, 0.0
            for cat, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：关键词模式匹配（同步，零延迟兜底）。"""
        if detect_emergency(message):
            return {"intent": IntentCategory.EMERGENCY, "confidence": 1.0}
        msg = message.lower()
        specific_patterns = {
            IntentCategory.HUMAN_HANDOFF: ["转人工", "人工导诊", "找人工", "导诊联系电话", "联系导诊"],
            IntentCategory.APPOINTMENT_CANCEL: ["取消预约", "取消这条预约", "准备取消", "取消这次预约", "取消我的预约"],
            IntentCategory.APPOINTMENT_STATUS: ["预约记录", "我的预约", "预约状态", "预约是什么状态"],
            IntentCategory.APPOINTMENT_CREATE: ["帮我预约", "选择这个号", "就选第", "准备预约", "预约这个", "确认预约"],
            IntentCategory.SLOT_QUERY: ["号源", "还有号", "有号吗", "可选的号", "查号", "余号"],
            IntentCategory.REPORT_QUERY: ["解读报告", "检查报告", "化验单", "检验报告", "报告解读", "报告数值", "参考范围", "参考区间", "上传报告", "报告图片", "血红蛋白", "白细胞", "血小板"],
            IntentCategory.MEDICATION_QUERY: ["用药", "药物", "药品", "说明书", "禁忌", "相互作用", "同服", "布洛芬", "对乙酰氨基酚", "华法林", "阿司匹林"],
            IntentCategory.SYMPTOM_QUERY: ["挂哪个科", "看哪个科", "什么科室", "症状", "咳嗽", "流鼻涕", "鼻塞", "咽痛", "肚子疼", "腹痛", "眼睛发红", "眼睛痒", "眼睛发痒", "头痛", "发烧", "发热"],
            IntentCategory.VISIT_PREPARATION: ["带什么", "材料清单", "准备材料", "就诊材料", "就诊准备", "准备什么"],
            IntentCategory.VISIT_PROCESS: ["报到", "取号", "就诊流程", "到院后", "办理流程"],
            IntentCategory.HOSPITAL_INFO: ["医院几点", "门诊时间", "医院地址", "医院信息", "介绍一下医院", "医院介绍"],
            IntentCategory.WAYFINDING: ["怎么去", "怎么走", "无障碍路线", "普通路线", "在哪"],
            IntentCategory.DEPARTMENT_INFO: ["科室介绍", "科的介绍", "哪些科室", "科室说明", "介绍一下儿科"],
            IntentCategory.DOCTOR_INFO: ["医生", "医师", "出诊", "排班"],
        }
        generic_patterns = {
            IntentCategory.ESCALATION: ["升级处理", "进一步协助"],
            IntentCategory.COMPLAINT:  ["太差", "糟糕", "horrible", "等了很久"],
            IntentCategory.QUERY:      ["?", "？", "怎么", "什么", "status"],
            IntentCategory.REQUEST:    ["帮我", "需要", "please", "help"],
            IntentCategory.GREETING:   ["你好", "嗨", "hello", "hi"],
            IntentCategory.APPOINTMENT: ["预约", "挂号"],
            IntentCategory.GUIDANCE: ["指引", "就医安排"],
            IntentCategory.FEEDBACK: ["满意", "好评", "很棒", "谢谢"],
        }

        best_cat, best_score = self._best_pattern_match(msg, specific_patterns)
        if best_cat != IntentCategory.OTHER:
            return {"intent": best_cat, "confidence": best_score}

        best_cat, best_score = self._best_pattern_match(msg, generic_patterns)
        return {"intent": best_cat, "confidence": best_score}

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """加权投票。返回最终意图、融合置信度和各路来源得分。"""
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"], source_scores["embedding"], source_scores
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"], source_scores["pattern"], source_scores
            return IntentCategory.OTHER, 0.0, source_scores

        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
        else:
            weights = [(llm, 0.85), (pat, 0.15)]
        scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            scores[cat] = scores.get(cat, 0.0) + w * conf

        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if best in _GENERIC_INTENTS and pat_intent in _SPECIFIC_INTENTS and pat_conf >= 0.5 and best_score < 0.8:
            source_scores["refined_by_pattern"] = pat_conf
            return pat_intent, max(best_score, pat_conf), source_scores
        if best_score < self.threshold:
            return IntentCategory.OTHER, best_score, source_scores
        return best, best_score, source_scores

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    def _extract_entities(self, message: str, *, today: Optional[date] = None) -> Dict[str, List[str]]:
        """提取当前文本的查询条件；名称存在性和列表位置由业务服务验证。"""
        message = self._clean_text(message)
        today = today or self._today()
        entities: Dict[str, List[str]] = {key: [] for key in (
            "department", "doctor", "date", "period", "slot_id", "appointment_id",
            "origin", "destination", "accessibility", "selection_index",
        )}

        departments = re.findall(r"([\u4e00-\u9fff]{1,24}科)(?!室)", message)
        entities["department"] = self._unique([self._strip_entity_prefix(value) for value in departments])
        doctors = re.findall(r"([\u4e00-\u9fff]{1,16})(?:医生|医师|大夫)", message)
        doctors += re.findall(r"(?:医生|医师|大夫)\s*[:：]\s*([\u4e00-\u9fff]{1,4})", message)
        for value in doctors:
            name = self._strip_entity_prefix(value)
            for department in entities["department"]:
                name = name.removeprefix(department).removeprefix("的")
            if (1 <= len(name) <= 4 and "科" not in name
                    and name not in {"哪位", "哪个", "哪些", "什么", "有哪些", "有哪位", "出诊"}):
                entities["doctor"].append(name)

        for match in re.finditer(r"今天|明天|后天|(?<!\d)\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?!\d)", message):
            token = match.group()
            if token in {"今天", "明天", "后天"}:
                value = today + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[token])
            else:
                try:
                    value = date(*map(int, re.findall(r"\d+", token)))
                except ValueError:
                    continue  # 无效日期保持缺失，不替用户选择另一天。
            entities["date"].append(value.isoformat())

        for pattern, period in ((r"上午|早上|早晨|\bmorning\b", "morning"), (r"下午|\bafternoon\b", "afternoon")):
            if re.search(pattern, message, re.I):
                entities["period"].append(period)
        identifier = r"([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?![A-Za-z0-9_.:-])"
        for key, label in (
            ("slot_id", r"slot_id|号源(?:编号|ID|号)?"),
            ("appointment_id", r"appointment_id|预约(?:编号|ID|号)"),
        ):
            entities[key] = re.findall(rf"(?:{label})\s*[:：=]?\s*{identifier}", message, re.I)

        route = re.search(r"从([^，。！？,!?]{1,24}?)(?:怎么去|如何去|怎么到|如何到|到|去|前往)([^，。！？,!?]{1,24})", message)
        if route:
            entities["origin"] = [route.group(1).strip()]
            destination = re.sub(r"(?:怎么走|如何走|的?(?:无障碍|普通)?路线|[呢吗吧])+$", "", route.group(2)).strip()
            if destination:
                entities["destination"] = [destination]
        else:
            destination = re.search(r"([^，。！？,!?]{1,24}?)(?:在哪里|在哪儿|在哪)(?:[呢吗吧])?[？?]?$", message)
            if destination:
                entities["destination"] = [self._strip_entity_prefix(destination.group(1))]
        for key, label in (("origin", "起点"), ("destination", "目的地")):
            explicit = re.findall(rf"{label}\s*[:：]\s*([^，。！？,!?]{{1,24}})", message)
            if explicit:
                entities[key] = explicit

        if re.search(r"普通路线|(?:不用|不需要)无障碍", message):
            entities["accessibility"] = ["normal"]
        elif re.search(r"无障碍|轮椅", message):
            entities["accessibility"] = ["accessible"]
        for token in re.findall(r"第([0-9一二两三四五六七八九十]+)(?:个|项|条)", message):
            digits = {value: index for index, value in enumerate("零一二三四五六七八九")}
            digits["两"] = 2
            if token.isdigit():
                position = int(token)
            elif token in digits:
                position = digits[token]
            elif re.fullmatch(r"[一二三四五六七八九]?十[一二三四五六七八九]?", token):
                tens, units = token.split("十")
                position = digits.get(tens, 1) * 10 + digits.get(units, 0)
            else:
                continue
            if position > 0:
                entities["selection_index"].append(str(position))
        return {key: self._unique(values) for key, values in entities.items()}

    @staticmethod
    def _strip_entity_prefix(value: str) -> str:
        """仅去掉提问修饰语，不把名称猜成内部目录 ID。"""
        return re.sub(
            r"^(?:(?:请问|请|帮我|给我|我想|想|帮孩子|给孩子|孩子的|查询|查一下|查看|查查|查|看看|看|"
            r"介绍一下|介绍|了解一下|了解|明天|后天|今天|上午|下午|有哪些|哪些|预约|挂号|挂|一下|去|到|在|找|的)\s*)+",
            "", value,
        ).strip()

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """懒加载所有模板的 Embedding（只在首次调用时执行）。"""
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        if not missing:
            return

        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        vecs = [await self._embed_text(text) for text in all_texts]
        idx = 0
        for cat in missing:
            n = len(_TEMPLATES[cat])
            self._tpl_embeddings[cat] = vecs[idx: idx + n]
            idx += n

    async def _embed_text(self, text: str) -> List[float]:
        """
        生成文本向量。

        如果未来接入的官方/兼容客户端提供 embeddings.create，会优先使用远端向量；
        当前 Anthropic SDK 没有该资源时，退化为字符 n-gram 哈希向量。这样不会因为
        Embedding 服务缺失导致三路融合中断。
        """
        embeddings = getattr(self.client, "embeddings", None)
        if embeddings is not None:
            try:
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                return list(resp.data[0].embedding)
            except Exception as ex:
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。"""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        if is_emergency_reference(message):
            return UrgencyLevel.LOW
        if intent == IntentCategory.EMERGENCY and detect_emergency(message):
            return UrgencyLevel.CRITICAL
        msg = message.lower()
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(kw in msg for kw in kws):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def _today(self) -> date:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("分类器时钟必须返回带时区的 datetime")
        return now.astimezone(self._timezone).date()

    def _cache_key(
        self, message: str, history: Optional[List[Dict[str, str]]] = None, *, today: Optional[date] = None,
    ) -> str:
        payload = {"message": self._clean_text(message)[:200], "date": (today or self._today()).isoformat()}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", ""))[:20],
                    "content": self._clean_text(item.get("content", ""))[:160],
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _best_pattern_match(
        message: str,
        patterns: Dict[IntentCategory, List[str]],
    ) -> tuple[IntentCategory, float]:
        best_cat, best_score = IntentCategory.OTHER, 0.0
        for cat, kws in patterns.items():
            hits = sum(1 for kw in kws if kw in message)
            if not hits:
                continue
            # 单个明确业务关键词就给可用置信度；多个关键词命中时提高置信度。
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = score, cat
        return best_cat, best_score

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }
