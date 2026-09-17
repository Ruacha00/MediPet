import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.intent_recognizer import IntentCategory as Intent, IntentRecognizer, UrgencyLevel
from core.intent_recognizer import detect_emergency
from core.intent_embeddings import EmbeddingConfig, IntentEmbeddingProvider


NOW = datetime(2026, 9, 16, 2, tzinfo=timezone.utc)
ENTITY_KEYS = {
    "department", "doctor", "date", "period", "slot_id", "appointment_id",
    "origin", "destination", "accessibility", "selection_index",
}


@pytest.mark.parametrize("message", [
    "现在呼吸困难", "我现在呼吸困难，怎么办？", "有人突然失去意识", "现在剧烈胸痛",
    "不是现在呼吸困难，而是现在剧烈胸痛", "没有现在剧烈胸痛，但是有人突然失去意识",
])
def test_shared_emergency_detector_matches_only_current_affirmative_demo_signals(message):
    assert detect_emergency(message)


@pytest.mark.parametrize("message", [
    "没有出现该急症信号", "没有突然失去意识", "并非现在呼吸困难", "没有出现现在剧烈胸痛",
    "现在没有呼吸困难", "现在呼吸困难是什么意思？", "突然失去意识这种信号是什么意思？",
    "如果有人突然失去意识怎么办？", "昨天有人突然失去意识", "现在剧烈胸痛吗？", "请帮我查明天儿科号源",
])
def test_emergency_negations_and_general_questions_do_not_interrupt(message):
    assert not detect_emergency(message)


@pytest.mark.parametrize("message", ["并非现在呼吸困难", "突然失去意识是什么意思？", "如果有人突然失去意识怎么办？"])
def test_three_way_classifier_cannot_override_shared_emergency_exclusion(message):
    recognizer = make_recognizer("emergency")
    assert recognizer._pattern_recognize(message)["intent"] is not Intent.EMERGENCY
    assert recognizer._urgency(message, Intent.EMERGENCY) is UrgencyLevel.LOW
    result = asyncio.run(recognizer.recognize(message))
    assert result.intent is Intent.QUERY and result.urgency is UrgencyLevel.LOW
    assert set(result.source_scores) >= {"llm", "embedding", "pattern"}


class FakeClient:
    def __init__(self, intent="slot_query", *, error=None):
        self.intent = intent
        self.error = error
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({
            "intent": self.intent, "confidence": 0.95, "reasoning": "假模型分类",
        }))])


def make_recognizer(intent="slot_query", *, clock=lambda: NOW, error=None):
    client = FakeClient(intent, error=error)
    with patch("core.intent_recognizer.AsyncAnthropic", return_value=client):
        recognizer = IntentRecognizer(api_key="fake-key-for-tests", model="fake-model", clock=clock,
                                      embedding_provider=IntentEmbeddingProvider(EmbeddingConfig(backend="hash")))
    return recognizer


@pytest.mark.parametrize("message,expected,group", [
    ("医院几点开始门诊？", Intent.HOSPITAL_INFO, "query"),
    ("医院有哪些科室？", Intent.DEPARTMENT_INFO, "query"),
    ("许知宁医生什么时候出诊？", Intent.DOCTOR_INFO, "query"),
    ("明天儿科还有号吗？", Intent.SLOT_QUERY, "appointment"),
    ("选择这个号，帮我预约。", Intent.APPOINTMENT_CREATE, "appointment"),
    ("看一下孩子的预约记录。", Intent.APPOINTMENT_STATUS, "appointment"),
    ("取消这条预约。", Intent.APPOINTMENT_CANCEL, "appointment"),
    ("第一次就诊需要带什么？", Intent.VISIT_PREPARATION, "guidance"),
    ("到院后先在哪里报到？", Intent.VISIT_PROCESS, "guidance"),
    ("从门诊大厅怎么去药房？", Intent.WAYFINDING, "guidance"),
    ("我想联系人工导诊。", Intent.HUMAN_HANDOFF, "escalation"),
    ("现在呼吸困难", Intent.EMERGENCY, "escalation"),
])
def test_hospital_intents_have_working_patterns_and_three_way_classification(message, expected, group):
    recognizer = make_recognizer(expected.value)

    assert recognizer._pattern_recognize(message)["intent"] is expected
    result = asyncio.run(recognizer.recognize(message))

    assert result.intent is expected
    assert result.intent_group == group
    assert result.confidence >= recognizer.threshold
    assert set(result.source_scores) >= {"llm", "embedding", "pattern"}
    assert set(result.entities) == ENTITY_KEYS
    if expected is Intent.EMERGENCY:
        assert result.urgency is UrgencyLevel.CRITICAL


def test_actual_model_request_contains_hospital_examples_and_recent_context_only():
    recognizer = make_recognizer()
    history = [
        {"role": "user", "content": "不应包含的旧上下文"},
        {"role": "user", "content": "查一下儿科"},
        {"role": "assistant", "content": "请提供日期"},
        {"role": "user", "content": "明天"},
    ]

    asyncio.run(recognizer._llm_recognize("还是下午吧", history))

    call = recognizer.client.calls[0]
    prompt = call["messages"][0]["content"]
    assert "MediPet" in prompt
    assert '消息: "明天儿科还有号吗？" → 意图: slot_query' in prompt
    assert '消息: "第一次就诊需要带什么？" → 意图: visit_preparation' in prompt
    assert "查一下儿科" in prompt and "还是下午吧" in prompt
    assert "不应包含的旧上下文" not in prompt
    candidates = prompt.split("可选意图: ", 1)[1].split(", ")
    assert {"guidance", "appointment", "other", "greeting", "feedback"} <= set(candidates)
    assert not {"technical", "billing", "technical_login", "refund", "order_status"} & set(candidates)
    assert call["model"] == "fake-model"
    assert call["max_tokens"] == 256 and call["temperature"] == 0.1


@pytest.mark.parametrize("legacy", ["technical", "billing", "technical_login", "refund", "not_an_intent"])
def test_legacy_or_unknown_model_labels_are_not_classification_candidates(legacy):
    recognizer = make_recognizer(legacy)
    assert asyncio.run(recognizer._llm_recognize("需要帮助", None))["intent"] is Intent.OTHER


@pytest.mark.parametrize("embedding_enabled,expected", [(True, 0.72), (False, 0.74)])
def test_original_voting_weights_are_preserved(embedding_enabled, expected):
    recognizer = make_recognizer()
    recognizer._embedding_enabled = embedding_enabled
    intent, confidence, scores = recognizer._vote(
        {"intent": Intent.SLOT_QUERY, "confidence": 0.8},
        {"intent": Intent.SLOT_QUERY, "confidence": 0.6},
        {"intent": Intent.SLOT_QUERY, "confidence": 0.4},
    )
    assert intent is Intent.SLOT_QUERY
    assert confidence == pytest.approx(expected)
    assert scores == {"llm": 0.8, "embedding": 0.6, "pattern": 0.4}


@pytest.mark.parametrize("embedding_intent,embedding_conf,pattern_intent,pattern_conf,expected,score", [
    (Intent.DOCTOR_INFO, 0.2, Intent.SLOT_QUERY, 0.9, Intent.SLOT_QUERY, 0.9),
    (Intent.OTHER, 0.9, Intent.SLOT_QUERY, 0.5, Intent.OTHER, 0.0),
    (Intent.DOCTOR_INFO, 0.0, Intent.SLOT_QUERY, 0.5, Intent.OTHER, 0.0),
    (Intent.OTHER, 0.0, Intent.OTHER, 0.0, Intent.OTHER, 0.0),
])
def test_llm_failure_never_accepts_uncalibrated_positive_vector_score(
    embedding_intent, embedding_conf, pattern_intent, pattern_conf, expected, score,
):
    recognizer = make_recognizer()
    result = recognizer._vote(
        {"intent": Intent.OTHER, "confidence": 0.0, "failed": True},
        {"intent": embedding_intent, "confidence": embedding_conf},
        {"intent": pattern_intent, "confidence": pattern_conf},
    )
    assert result[0] is expected
    assert result[1] == score


def test_disabled_embedding_and_failed_llm_reach_real_keyword_fallback():
    recognizer = make_recognizer(error=RuntimeError("fake provider unavailable"))
    recognizer._embedding_enabled = False

    async def unexpected_embedding(message):
        raise AssertionError("disabled embedding must not run")

    recognizer._embedding_recognize = unexpected_embedding
    result = asyncio.run(recognizer.recognize("明天儿科还有号吗？"))
    assert result.intent is Intent.SLOT_QUERY
    assert result.reasoning == "LLM 失败"
    assert result.source_scores == {"llm": 0.0, "embedding": 0.0, "pattern": 0.75}


def test_specific_refinement_and_confidence_threshold_keep_original_order():
    recognizer = make_recognizer()
    result = recognizer._vote(
        {"intent": Intent.APPOINTMENT, "confidence": 0.8},
        {"intent": Intent.APPOINTMENT, "confidence": 0.9},
        {"intent": Intent.SLOT_QUERY, "confidence": 0.5},
    )
    assert result[0] is Intent.SLOT_QUERY
    assert result[1] == pytest.approx(0.74)
    assert result[2]["refined_by_pattern"] == 0.5
    result = recognizer._vote(*[{"intent": Intent.SLOT_QUERY, "confidence": 0.1}] * 3)
    assert result[0] is Intent.OTHER
    assert result[1] == pytest.approx(0.1)


@pytest.mark.parametrize("message,expected", [
    ("今天", ["2026-09-17"]),
    ("明天", ["2026-09-18"]),
    ("后天", ["2026-09-19"]),
    ("2026-10-01", ["2026-10-01"]),
    ("2026年10月1日", ["2026-10-01"]),
    ("2026/10/1", ["2026-10-01"]),
    ("2028.2.29", ["2028-02-29"]),
    ("2026-02-30", []),
    ("2026-13-01", []),
    ("改天或者下周", []),
    ("什么时候有空？", []),
])
def test_dates_use_shanghai_day_and_only_valid_explicit_dates(message, expected):
    clock = lambda: datetime(2026, 9, 16, 16, 1, tzinfo=timezone.utc)
    recognizer = make_recognizer(clock=clock)
    assert recognizer._extract_entities(message)["date"] == expected


def test_entities_match_h01_contract_without_resolving_names_to_ids():
    recognizer = make_recognizer()
    entities = recognizer._extract_entities(
        "查询明天上午儿科，许知宁医生，号源编号：slot-abc，预约编号：appointment-def，"
        "从门诊大厅到药房怎么走，需无障碍路线，选择第一个"
    )
    assert entities == {
        "department": ["儿科"], "doctor": ["许知宁"], "date": ["2026-09-17"],
        "period": ["morning"], "slot_id": ["slot-abc"], "appointment_id": ["appointment-def"],
        "origin": ["门诊大厅"], "destination": ["药房"], "accessibility": ["accessible"],
        "selection_index": ["1"],
    }


@pytest.mark.parametrize("message,expected", [
    ("选第一个", ["1"]), ("选择第2项", ["2"]), ("第十条", ["10"]),
    ("第十二个", ["12"]), ("第二十一个", ["21"]), ("第0项", []),
])
def test_selection_returns_one_based_string_position_only(message, expected):
    entities = make_recognizer()._extract_entities(message)
    assert entities["selection_index"] == expected
    assert entities["slot_id"] == []
    assert entities["appointment_id"] == []


def test_contextual_selection_does_not_guess_ids_or_replace_identity():
    recognizer = make_recognizer("appointment_create")
    history = [{"role": "assistant", "content": "号源列表：1. 许知宁，上午；2. 许知宁，下午"}]
    result = asyncio.run(recognizer.recognize("我是另一个患者，选第一个，还是下午吧", history))
    assert result.entities["selection_index"] == ["1"]
    assert result.entities["period"] == ["afternoon"]
    assert result.entities["slot_id"] == [] and result.entities["appointment_id"] == []
    assert not {"patient_id", "user_id", "conv_id"} & result.entities.keys()
    assert "号源列表" in recognizer.client.calls[0]["messages"][0]["content"]


def test_unknown_department_and_vague_input_do_not_become_valid_booking_data():
    recognizer = make_recognizer()
    unknown = recognizer._extract_entities("火星科还有号吗？")
    assert unknown["department"] == ["火星科"]
    assert unknown["date"] == [] and unknown["slot_id"] == []
    vague = recognizer._extract_entities("改天看看那个医生的号")
    assert vague["doctor"] == [] and vague["date"] == [] and vague["slot_id"] == []


@pytest.mark.parametrize("message,department,doctor", [
    ("明天有哪些儿科医生出诊？", ["儿科"], []),
    ("明天儿科许知宁医生还有号吗？", ["儿科"], ["许知宁"]),
    ("我想查明天内科的号", ["内科"], []),
])
def test_department_and_doctor_names_exclude_query_prefixes(message, department, doctor):
    entities = make_recognizer()._extract_entities(message)
    assert entities["department"] == department
    assert entities["doctor"] == doctor


@pytest.mark.parametrize("message,expected", [
    ("医院地址在哪里？", Intent.HOSPITAL_INFO),
    ("取消我的预约", Intent.APPOINTMENT_CANCEL),
    ("就选第一个号源", Intent.APPOINTMENT_CREATE),
])
def test_specific_business_patterns_win_over_overlapping_generic_words(message, expected):
    assert make_recognizer()._pattern_recognize(message)["intent"] is expected


@pytest.mark.parametrize("message,origin,destination,accessibility", [
    ("从门诊大厅怎么去药房？", ["门诊大厅"], ["药房"], []),
    ("从收费处到检验科的无障碍路线", ["收费处"], ["检验科"], ["accessible"]),
    ("起点：门诊大厅，目的地：药房，普通路线", ["门诊大厅"], ["药房"], ["normal"]),
    ("药房在哪里？", [], ["药房"], []),
])
def test_wayfinding_entities_preserve_given_places(message, origin, destination, accessibility):
    entities = make_recognizer()._extract_entities(message)
    assert entities["origin"] == origin
    assert entities["destination"] == destination
    assert entities["accessibility"] == accessibility


def test_relative_date_cache_expires_across_shanghai_midnight():
    current = [datetime(2026, 9, 16, 15, 59, tzinfo=timezone.utc)]
    recognizer = make_recognizer(clock=lambda: current[0])
    first = asyncio.run(recognizer.recognize("明天儿科还有号吗？"))
    cached = asyncio.run(recognizer.recognize("明天儿科还有号吗？"))
    assert cached.intent is first.intent
    assert cached.embedding_info["cache_hit"] is True
    assert cached.embedding_info["elapsed_ms"] is None
    assert first.entities["date"] == ["2026-09-17"]
    assert len(recognizer.client.calls) == 1
    current[0] = datetime(2026, 9, 16, 16, 1, tzinfo=timezone.utc)
    next_day = asyncio.run(recognizer.recognize("明天儿科还有号吗？"))
    assert next_day.entities["date"] == ["2026-09-18"]
    assert len(recognizer.client.calls) == 2
    assert recognizer.cache_stats["hits"] == 1


def test_cache_still_distinguishes_recent_context():
    recognizer = make_recognizer()
    for previous in ("查询儿科号源", "查询内科号源"):
        asyncio.run(recognizer.recognize("明天呢", [{"role": "user", "content": previous}]))
    assert len(recognizer.client.calls) == 2


def test_explicit_hash_vector_is_repeatable_local_256_dimensional_character_hash():
    recognizer = make_recognizer()
    first = asyncio.run(recognizer._embed_text("儿科号源"))
    assert len(first) == 256
    assert first == recognizer._local_embedding("儿科号源")
    assert any(first)
    assert recognizer.client.calls == []


class MemoryEmbeddingProvider:
    """Async test double: no threads, model downloads, or semantic claims."""
    def __init__(self):
        self.space = "test:a"
        self.generation = 1
        self.calls = []
        self.after_batch = None

    def status(self):
        return {"configured_backend": "semantic", "active_backend": "semantic", "model": "fake",
                "space_id": self.space, "generation": self.generation, "dimension": 256,
                "status": "semantic_ready", "fallback_reason": None}

    async def initialize(self):
        return self.status()

    async def encode_batch(self, texts):
        from core.intent_embeddings import EmbeddingBatch, hash_embedding
        self.calls.append(list(texts))
        await asyncio.sleep(0)
        if self.after_batch:
            self.after_batch(texts)
        return EmbeddingBatch([hash_embedding(text) for text in texts], self.space, self.generation,
                              "semantic", "fake", 256, 1.0, [False] * len(texts))


def wired_recognizer():
    recognizer = make_recognizer()
    recognizer._embedding_provider = MemoryEmbeddingProvider()
    return recognizer


def test_message_and_full_history_cache_fingerprints_do_not_truncate_tail():
    r = wired_recognizer()
    assert r._cache_key("字" * 200 + "甲") != r._cache_key("字" * 200 + "乙")
    h1 = [{"role": "user", "content": "字" * 160 + "甲"}]
    h2 = [{"role": "user", "content": "字" * 160 + "乙"}]
    assert r._cache_key("明天呢", h1) != r._cache_key("明天呢", h2)


def test_cosine_rejects_incompatible_or_invalid_vectors():
    from core.intent_recognizer import _cosine
    for a, b in [([1], [1, 2]), ([float("nan")], [1]), ([0], [1]), ([], [])]:
        with pytest.raises(ValueError):
            _cosine(a, b)


@pytest.mark.asyncio
async def test_concurrent_first_queries_publish_one_template_snapshot():
    r = wired_recognizer()
    await asyncio.gather(r._embedding_recognize("明天儿科还有号吗？"),
                         r._embedding_recognize("医院几点开始门诊？"))
    assert sum(len(texts) > 1 for texts in r._embedding_provider.calls) == 1
    assert r._tpl_identity == ("test:a", 1, 0)


@pytest.mark.asyncio
async def test_query_space_switch_rebuilds_all_templates_even_at_same_dimension():
    r = wired_recognizer()
    provider = r._embedding_provider
    def switch(texts):
        if len(texts) == 1 and provider.space == "test:a":
            provider.space = "test:b"
            provider.generation += 1
    provider.after_batch = switch
    result = await r._embedding_recognize("明天儿科还有号吗？")
    assert r._tpl_identity == ("test:b", 2, 0)
    assert sum(len(texts) > 1 for texts in provider.calls) == 2
    assert result["info"]["space_id"] == "test:b"


@pytest.mark.asyncio
async def test_learn_during_build_cannot_publish_old_snapshot_or_cross_instance():
    from core.intent_recognizer import _TEMPLATES
    r, untouched = wired_recognizer(), wired_recognizer()
    provider = r._embedding_provider
    def learn_once(texts):
        provider.after_batch = None
        r.learn("一条只在当前实例学习的新表述", Intent.SLOT_QUERY)
    provider.after_batch = learn_once
    result = await r._embedding_recognize("明天儿科还有号吗？")
    assert r._tpl_identity == ("test:a", 1, 1)
    assert result["info"]["template_revision"] == 1
    assert "一条只在当前实例学习的新表述" not in untouched._templates[Intent.SLOT_QUERY]
    assert "一条只在当前实例学习的新表述" not in _TEMPLATES[Intent.SLOT_QUERY]


@pytest.mark.asyncio
async def test_uncalibrated_vectors_return_raw_ranking_but_abstain_from_vote():
    r = wired_recognizer()
    r._calibration = {}
    result = await r._embedding_recognize("明天儿科还有号吗？")
    assert result["ranking"][0]["intent"] == "slot_query"
    assert result["info"]["top1"] == pytest.approx(1)
    assert result["confidence"] == 0 and not result["accepted"]
    assert result["info"]["calibration_status"] == "uncalibrated"
    assert len({item["intent"] for item in result["ranking"]}) == len(result["ranking"])


@pytest.mark.asyncio
async def test_learn_during_llm_request_does_not_repopulate_stale_result_cache():
    r = wired_recognizer()
    original = r._llm_recognize
    async def learns(message, history):
        r.learn("追加号源问法", Intent.SLOT_QUERY)
        return await original(message, history)
    r._llm_recognize = learns
    await r.recognize("明天儿科还有号吗？")
    assert r._cache == {}


@pytest.mark.asyncio
async def test_failed_llm_is_not_saved_in_regular_result_cache():
    r = make_recognizer(error=RuntimeError("unavailable"))
    await r.recognize("明天儿科还有号吗？")
    await r.recognize("明天儿科还有号吗？")
    assert len(r.client.calls) == 2
    assert r._cache == {}


@pytest.mark.asyncio
async def test_emergency_bypasses_both_model_branches_before_initialization():
    r = wired_recognizer()
    result = await r.recognize("我现在呼吸困难")
    assert result.intent is Intent.EMERGENCY
    assert r.client.calls == [] and r._embedding_provider.calls == []
    assert result.embedding_info["status"] == "bypassed_emergency"


@pytest.mark.parametrize("backend,accepted,standalone,pattern,expected", [
    ("semantic", True, True, Intent.OTHER, Intent.SLOT_QUERY),
    ("semantic", False, False, Intent.OTHER, Intent.OTHER),
    ("semantic", True, False, Intent.OTHER, Intent.OTHER),
    ("hash", True, True, Intent.OTHER, Intent.OTHER),
    ("hash", True, False, Intent.SLOT_QUERY, Intent.SLOT_QUERY),
    ("hash", False, False, Intent.SLOT_QUERY, Intent.OTHER),
])
def test_failure_policy_distinguishes_semantic_hash_and_template_adaptation(backend, accepted, standalone, pattern, expected):
    r = wired_recognizer()
    result = r._vote({"intent": Intent.OTHER, "confidence": 0.0, "failed": True},
                     {"intent": Intent.SLOT_QUERY, "confidence": 0.9 if accepted else 0,
                      "backend": backend, "accepted": accepted, "standalone": standalone},
                     {"intent": pattern, "confidence": 0.5 if pattern is not Intent.OTHER else 0})
    assert result[0] is expected


def test_failure_conflicting_strong_rules_or_semantic_signal_abstain():
    r = wired_recognizer()
    failed = {"intent": Intent.OTHER, "confidence": 0.0, "failed": True}
    strong = {"intent": Intent.SLOT_QUERY, "confidence": 0.9}
    semantic = {"intent": Intent.DOCTOR_INFO, "confidence": 0.95,
                "backend": "semantic", "accepted": True, "standalone": True}
    assert r._vote(failed, semantic, strong)[0] is Intent.OTHER
    rules = {**strong, "matches": [{"intent": "slot_query", "confidence": 0.9},
                                  {"intent": "doctor_info", "confidence": 0.9}]}
    assert r._vote(failed, {"intent": Intent.OTHER, "confidence": 0}, rules)[0] is Intent.OTHER


@pytest.mark.asyncio
async def test_calibration_controls_margin_and_learn_retains_only_normal_fusion():
    r = wired_recognizer()
    r._calibration = {"template_fingerprint": r.template_fingerprint,
                      "backends": {"semantic": {"space_id": "test:a", "min_score": 0.8, "min_margin": 0}}}
    before = await r._embedding_recognize("明天儿科还有号吗？")
    assert before["accepted"] and before["standalone"]
    assert before["info"]["margin"] == pytest.approx(before["ranking"][0]["score"] - before["ranking"][1]["score"])
    r.learn("全新查号句子", Intent.SLOT_QUERY)
    after = await r._embedding_recognize("明天儿科还有号吗？")
    assert after["accepted"] and not after["standalone"]
    assert after["info"]["template_changed_since_calibration"]
    r._calibration["backends"]["semantic"]["min_margin"] = 2
    assert not (await r._embedding_recognize("明天儿科还有号吗？"))["accepted"]


@pytest.mark.asyncio
async def test_context_only_text_never_gets_standalone_vector_authority():
    r = wired_recognizer()
    r._calibration = {"template_fingerprint": r.template_fingerprint,
                      "backends": {"semantic": {"space_id": "test:a", "min_score": -1, "min_margin": 0}}}
    result = await r._embedding_recognize("明天呢")
    assert not result["standalone"] and result["info"]["requires_context"]


def test_disabled_fallback_uses_existing_two_branch_weights():
    r = wired_recognizer()
    original = r._embedding_provider.status
    r._embedding_provider.status = lambda: {**original(), "active_backend": "disabled", "status": "disabled_fallback"}
    result = r._vote({"intent": Intent.SLOT_QUERY, "confidence": 0.8},
                     {"intent": Intent.OTHER, "confidence": 0},
                     {"intent": Intent.SLOT_QUERY, "confidence": 0.4})
    assert result[1] == pytest.approx(0.74)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "learn"])
async def test_vector_completed_before_llm_cannot_vote_after_snapshot_changes(change):
    r = wired_recognizer()
    ready, resume = asyncio.Event(), asyncio.Event()
    async def vector(message):
        result = {"intent": Intent.SLOT_QUERY, "confidence": 0.95,
                  "backend": "semantic", "accepted": True, "standalone": True,
                  "info": {**r._embedding_provider.status(), "template_revision": 0, "accepted": True}}
        ready.set()
        return result
    async def llm(message, history):
        await resume.wait()
        return {"intent": Intent.OTHER, "confidence": 0, "failed": True}
    r._embedding_recognize, r._llm_recognize = vector, llm
    pending = asyncio.create_task(r.recognize("另一天是否尚有余量"))
    await ready.wait()
    if change == "generation":
        r._embedding_provider.generation += 1
    else:
        r.learn("新的表达", Intent.SLOT_QUERY)
    resume.set()
    result = await pending
    assert result.intent is Intent.OTHER
    assert result.source_scores["embedding"] == 0
    assert result.embedding_info["stale_snapshot"]
    assert not result.embedding_info["accepted"]
    assert r._cache == {}


@pytest.mark.asyncio
async def test_explicit_disabled_result_still_caches_without_false_stale_snapshot():
    r = make_recognizer()
    r._embedding_provider = IntentEmbeddingProvider(EmbeddingConfig(backend="disabled"))
    r._embedding_enabled = False
    first = await r.recognize("另一天下午是否有空位")
    second = await r.recognize("另一天下午是否有空位")
    assert not first.embedding_info.get("stale_snapshot")
    assert second.embedding_info["cache_hit"]
    assert len(r.client.calls) == 1
    await r._embedding_provider.aclose()
