"""急症规则的有界正反例及真实 API 接线（模型/存储替身，无模型请求）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.emergency import EMERGENCY_RESPONSE, detect_emergency, is_emergency_reference
from core.intent_recognizer import UrgencyLevel
from hospital.models import VisitIdentity
from tests.test_chat_api import wired_api  # noqa: F401 — 显式共享接口装配 fixture


@pytest.mark.parametrize("message", [
    "我胸痛", "胸痛挂哪个科", "我现在胸口疼痛", "胸部疼痛", "我现在胸口剧烈疼痛", "孩子呼吸困难怎么办？",
    "我喘不过气了", "现在喘不上气", "他无法呼吸", "有人意识丧失", "我爸昏迷了", "胸痛没有缓解",
    "有人突然失去意识", "他不省人事", "孩子叫不醒",
    "我现在体温39.6度", "孩子发烧39.6℃", "现在体温４０．１℃", "发热40°C",
    "体温39.6摄氏度", "体温大于39.5度", "体温>39.5℃", "体温≥39.6℃",
    "39.6度", "体温39.6-40℃", "体温39.6℃到40℃", "体温40～39.6度",
    "没有胸痛但呼吸困难", "没有呼吸困难，而是现在胸痛", "以前胸痛，现在呼吸困难",
    "昨天发烧38度，今天体温40℃", "如果以后胸痛怎么办？现在有人昏迷了",
    "请科普胸痛。孩子现在呼吸困难", "没有胸痛；体温39.8度",
    "室温26度，我现在体温40度", "室温26度而我体温40度",
    "我没有发热并且现在呼吸困难", "我没有胸痛且现在呼吸困难",
    "没有呼吸困难且现在胸痛", "昨天没有发热并且现在体温40度", "没有胸痛且现在意识丧失",
])
def test_current_affirmative_signals_trigger_fixed_emergency(message):
    assert detect_emergency(message)
    assert not is_emergency_reference(message)


@pytest.mark.parametrize("message", [
    "我没有胸痛", "无呼吸困难", "否认昏迷", "不是胸口疼", "未出现意识丧失",
    "现在没有喘不过气", "没有胸痛、呼吸困难或昏迷", "不伴胸痛", "不再胸痛",
    "现在胸痛吗？", "有没有胸痛？", "是否呼吸困难？", "现在胸痛已经没有了",
    "昨天胸痛，现在没有了", "去年曾经昏迷", "胸痛是昨天的情况", "三天前呼吸困难", "胸痛没了",
    "以前发烧，体温40度", "昨天体温40度，今天已经退烧", "昨天发烧但是体温40度",
    "如果胸痛怎么办", "假如有人昏迷", "万一呼吸困难怎么处理", "会不会胸痛",
    "如果发烧，体温40℃怎么办", "假设没有胸痛，体温40℃怎么办", "如果发烧但体温40度怎么办",
    "胸痛是什么意思？", "解释一下昏迷", "科普呼吸困难", "如何识别意识丧失", "什么是昏迷", "胸痛是什么？",
    "科普发热，体温40℃的含义", "体温39.6度是什么意思",
    "如果体温超过39.5℃怎么办", "我没有发烧到40℃",
    "没有呼吸困难和胸痛", "没有呼吸困难且现在没有胸痛", "并非现在呼吸困难且现在没有胸痛",
    "如果没有发热并且现在呼吸困难怎么办", "没有胸痛且现在呼吸困难是什么意思",
])
def test_non_current_or_non_affirmative_signal_is_reference_only(message):
    assert not detect_emergency(message)
    assert is_emergency_reference(message)


@pytest.mark.parametrize("message", [
    "我现在体温39.5℃", "体温39.50度", "体温39.4度", "体温39.5℃以上",
    "体温≥39.5℃", "体温大于39.4度", "体温不到40度", "体温≤40℃",
    "体温40℃以下", "体温不超过40度", "体温39.5-40度", "体温39-40℃",
    "体温39.5℃至39.6℃", "体温38.5～39.5℃", "体温39.6到39.5度",
    "体温40度华氏", "体温104°F", "体温104度", "体温400℃", "体温99.9℃",
    "今天气温40度", "室温39.6℃", "水温40度", "空调调到40℃", "体温40", "镜头旋转40度", "我把水烧到40度",
    "请查明天儿科号源", "普通感冒怎么预防", "", None, 40,
])
def test_temperature_boundary_non_body_values_and_unmatched_input_do_not_trigger(message):
    assert not detect_emergency(message)


def test_fixed_response_identifies_china_and_local_emergency_number_without_diagnosis():
    assert "中国" in EMERGENCY_RESPONSE and "120" in EMERGENCY_RESPONSE
    assert "境外" in EMERGENCY_RESPONSE and "当地急救电话" in EMERGENCY_RESPONSE
    assert "不要等待在线聊天回复" in EMERGENCY_RESPONSE
    assert "不能替代医生诊断" in EMERGENCY_RESPONSE


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "胸痛挂哪个科", "孩子呼吸困难", "有人昏迷了", "孩子体温39.6℃",
    "我没有发热并且现在呼吸困难", "我没有胸痛且现在呼吸困难",
])
async def test_new_signals_bypass_models_tools_and_compression_through_chat(wired_api, monkeypatch, message):
    ctx = wired_api
    forbidden = AsyncMock(side_effect=AssertionError("急症不调用普通模型、召回、画像或工具"))
    monkeypatch.setattr(ctx.memory, "get_context", forbidden)
    monkeypatch.setattr(ctx.memory, "update_profile", forbidden)
    ctx.memory._client.messages.create = forbidden
    ctx.orch.recognize_intent = forbidden
    for pool in ctx.orch._pool.values():
        for agent in pool:
            agent._client = SimpleNamespace(messages=SimpleNamespace(create=forbidden))
            if agent.agent_type.value == "escalation":
                escalation = AsyncMock(wraps=agent.handle)
                monkeypatch.setattr(agent, "handle", escalation)
    append_window = AsyncMock(wraps=ctx.memory.add_message)
    monkeypatch.setattr(ctx.memory, "add_message", append_window)

    response = await ctx.client.post("/chat", json={"message": message, "patient_id": "patient_child"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["intent"] == "emergency" and data["primary_agent"] == "escalation"
    assert data["escalated"] and EMERGENCY_RESPONSE in data["response"]
    assert data["tools_used"] == data["tool_traces"] == []
    assert data["patient_id"] == "patient_child" and forbidden.call_count == 0
    assert escalation.await_args.args[0].urgency is UrgencyLevel.CRITICAL
    assert append_window.await_count == 2
    assert all(call.kwargs["compress"] is False for call in append_window.await_args_list)
    identity = VisitIdentity(user_id="anonymous", patient_id="patient_child", conv_id=data["conv_id"])
    history = (await ctx.client.get(f"/visits/{data['conv_id']}/messages")).json()["items"]
    window = await ctx.memory._get_working_memory(identity)
    assert len(history) == len(window) == 2
    assert history[0]["content"] == window[0].content == message


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["昨天胸痛，现在没有了", "如果昏迷怎么办", "没有呼吸困难", "体温39.5℃"])
async def test_counterexamples_reach_regular_chat(wired_api, message):
    response = await wired_api.client.post("/chat", json={"message": message})
    assert response.status_code == 200
    assert response.json()["intent"] != "emergency"
    assert wired_api.orch.recognize_intent.call_count == 1
