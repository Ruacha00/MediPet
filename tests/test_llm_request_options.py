"""可选 thinking 请求参数；所有调用使用假 SDK，不发送模型请求。"""
from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import AgentResponse, AgentType, GeneralAgent, ResponseComposer
from core.llm_utils import extract_text_content, llm_request_options
from evaluation.evaluator import LLMJudge
from hospital.models import VisitIdentity
from hospital.service import HospitalService
from mcp.tool_manager import MCPToolManager
from memory.conversation_memory import MemoryManager, MsgRole
from memory.visit_store import VisitStore
from test_agent_orchestrator import FakeClient, make_request
from test_conversation_memory import Chroma, Model
from test_intent_recognizer import make_recognizer
from test_visit_memory import MemoryRedis


@pytest.mark.parametrize("value", [None, "", "  "])
def test_unset_thinking_preserves_original_sdk_arguments(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MEDIPET_THINKING", raising=False)
    else:
        monkeypatch.setenv("MEDIPET_THINKING", value)
    assert llm_request_options() == {}


def test_disabled_options_are_explicit_and_independent(monkeypatch):
    monkeypatch.setenv("MEDIPET_THINKING", " Disabled ")
    options = llm_request_options()
    assert options == {"extra_body": {"thinking": {"type": "disabled"}}}
    options["extra_body"]["thinking"]["type"] = "modified"
    assert llm_request_options()["extra_body"]["thinking"]["type"] == "disabled"
    monkeypatch.setenv("MEDIPET_THINKING", "unsupported")
    with pytest.raises(ValueError, match="MEDIPET_THINKING"):
        llm_request_options()


def test_text_extraction_still_ignores_thinking_blocks():
    assert extract_text_content([
        {"type": "thinking", "thinking": "private reasoning"},
        SimpleNamespace(type="text", text='["query"]'),
    ]) == '["query"]'


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking", [None, "disabled"])
async def test_all_nine_request_paths_preserve_budgets_and_receive_only_explicit_option(monkeypatch, thinking):
    if thinking is None:
        monkeypatch.delenv("MEDIPET_THINKING", raising=False)
    else:
        monkeypatch.setenv("MEDIPET_THINKING", thinking)
    monkeypatch.setenv("MEDIPET_COMPOSER_MAX_TOKENS", "1000")
    monkeypatch.setenv("MEDIPET_COMPOSER_TEMPERATURE", "0.1")
    calls = []

    # Agent 的正常工具声明和输出上限保持不变。
    agent_client = FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="医院资料")]))
    agent = GeneralAgent(agent_client, "test-model")
    result = await agent.handle(make_request())
    assert result.success and agent_client.calls[0]["tools"]
    calls.append((agent_client.calls[0], agent.profile.max_tokens, agent.profile.temperature))

    composer_client = FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="综合资料")]))
    composer = ResponseComposer(composer_client, "test-model")
    result = await composer.compose(make_request(), [
        AgentResponse(AgentType.APPOINTMENT, "号源资料", True), AgentResponse(AgentType.GUIDANCE, "材料清单", True),
    ])
    assert result == "综合资料"
    calls.append((composer_client.calls[0], 1000, .1))

    recognizer = make_recognizer()
    await recognizer._llm_recognize("明天儿科还有号吗", None)
    calls.append((recognizer.client.calls[0], 256, .1))

    # 同一真实 MemoryManager 走画像、窗口压缩、摘要合并三个入口。
    redis, memory_client = MemoryRedis(), Model()
    monkeypatch.setattr("memory.conversation_memory.AsyncAnthropic", lambda **kwargs: memory_client)
    visits = VisitStore(redis, prefix="medipet:test:request-options:")
    await visits.initialize_patients(HospitalService().data.patients)
    visit = await visits.create_visit("anonymous", "patient_child")
    identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
    memory = MemoryManager(api_key="fake", redis_client=redis, chroma_client=Chroma(), visit_store=visits)
    await memory.add_message(identity, MsgRole.USER, "孩子明确说明的资料")
    memory_client.reply = '{"patient_facts":[],"preferences":[]}'
    await memory.update_profile(identity)
    memory_client.reply = "事项摘要"
    await redis.set(memory._summary_key(identity), "已有摘要")
    for index in range(14):
        await memory.add_message(identity, MsgRole.USER, f"就诊消息{index}")
    assert len(memory_client.calls) == 3
    calls.extend((call, budget, .0) for call, budget in zip(memory_client.calls, (512, 256, 256)))

    rag_client = Model()
    manager = MCPToolManager.__new__(MCPToolManager)
    manager._client, manager._model = rag_client, "test-model"
    rag_client.reply = '["材料","门诊报到","就诊流程"]'
    rewritten, error = await manager.rewrite_query("就诊准备")
    assert error is None and rewritten[0] == "就诊准备"
    rag_client.reply = "[2,0,1]"
    reranked, used, error = await manager._rerank("查询", ["a", "b", "c"], 1)
    assert used and error is None and reranked == ["c"]
    calls.extend([(rag_client.calls[0], 256, .3), (rag_client.calls[1], 256, .0)])

    judge_client = Model()
    judge_client.reply = '{"relevance":1,"accuracy":1,"completeness":1,"helpfulness":1}'
    score = await LLMJudge(judge_client, "test-model").judge("问题", "答案")
    assert not score.judge_failed and score.overall == 1
    calls.append((judge_client.calls[0], 256, .0))

    assert len(calls) == 9
    for request, budget, temperature in calls:
        assert request["max_tokens"] == budget and request["temperature"] == temperature
        assert request["messages"]
        if thinking is None:
            assert "extra_body" not in request and "thinking" not in request
        else:
            assert request["extra_body"] == {"thinking": {"type": "disabled"}}
