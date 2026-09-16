import asyncio
import json
from types import SimpleNamespace
from pathlib import Path
import shutil

import pytest

from mcp import knowledge_base as kb_module
from mcp import tool_manager as tm_module
from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import CircuitState, MCPToolManager, Tool
from core.skill_loader import SkillManager


def test_hospital_skills_roles_keywords_and_permanent_boundaries():
    manager = SkillManager(str(Path(__file__).parents[1] / "skills"))
    skills = manager.load()
    assert manager.errors == [] and len(skills) == 6
    assert {Path(skill.path).parent.name for skill in skills} == {"general_visit", "appointment_assistance", "visit_guidance", "service_boundaries", "health_triage", "medication_information"}
    for role in ("general", "guidance", "appointment", "escalation", "triage", "medication"):
        prompt = manager.prompt_for("嗯", role)
        assert "就诊助手服务边界" in prompt
        assert "模型只能查询或准备资料" in prompt
    assert "### 医院公开信息" in manager.prompt_for("儿科有哪些医生", "general")
    assert "### 医院公开信息" not in manager.prompt_for("嗯", "general")
    assert "预约与取消协助" in manager.prompt_for("第一个", "appointment")
    assert "预约与取消协助" not in manager.prompt_for("第一个", "guidance")
    assert "材料流程与院内指引" in manager.prompt_for("怎么过去", "guidance")
    assert "材料流程与院内指引" not in manager.prompt_for("怎么过去", "appointment")
    assert all(set(skill.agents) <= {"general", "guidance", "appointment", "escalation", "triage", "medication"} for skill in skills)


def test_skill_changes_apply_only_after_explicit_reload(tmp_path):
    source = Path(__file__).parents[1] / "skills"
    shutil.copytree(source, tmp_path / "skills")
    manager = SkillManager(str(tmp_path / "skills"))
    manager.load()
    path = tmp_path / "skills/appointment_assistance/SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n新增核验标记：先核对卡片的就诊人。\n", encoding="utf-8")
    assert "新增核验标记" not in manager.prompt_for("第一个", "appointment")
    manager.reload()
    assert "新增核验标记" in manager.prompt_for("第一个", "appointment")
    assert "新增核验标记" not in manager.prompt_for("第一个", "general")
    assert manager.summary()["count"] == 6 and manager.summary()["errors"] == []


class Collection:
    def __init__(self):
        self.records = {}
        self.upserts = 0

    def get(self, ids=None, include=(), where=None):
        keys = [key for key in (ids if ids is not None else self.records) if key in self.records]
        if where:
            keys = [key for key in keys if all(self.records[key][1].get(k) == v for k, v in where.items())]
        return {"ids": keys, "documents": [self.records[key][0] for key in keys],
                "metadatas": [self.records[key][1] for key in keys]}

    def add(self, ids, documents, metadatas):
        assert not set(ids) & self.records.keys()
        self.records.update(zip(ids, zip(documents, metadatas)))

    def upsert(self, ids, documents, metadatas):
        self.upserts += len(ids)
        self.records.update(zip(ids, zip(documents, metadatas)))

    def delete(self, ids):
        for key in ids:
            self.records.pop(key, None)

    def count(self):
        return len(self.records)

    def query(self, query_texts, n_results):
        # 可预测的关键词替身；不声称验证真实向量检索质量。
        matches = [(key, doc, meta) for key, (doc, meta) in self.records.items()
                   if query_texts[0] in doc][:n_results]
        return {"ids": [[row[0] for row in matches]], "documents": [[row[1] for row in matches]],
                "metadatas": [[row[2] for row in matches]], "distances": [[0.1] * len(matches)]}


@pytest.fixture
def fake_chroma(monkeypatch):
    collection = Collection()
    names = []

    def get_or_create_collection(name, metadata):
        names.append(name)
        return collection

    client = SimpleNamespace(heartbeat=lambda: None, get_or_create_collection=get_or_create_collection)
    monkeypatch.setattr(kb_module.chromadb, "HttpClient", lambda **kwargs: client)
    monkeypatch.setattr(kb_module.chromadb, "PersistentClient", lambda **kwargs: client)
    return collection, names


def test_hospital_initialization_is_idempotent_and_fills_partial_collection(fake_chroma):
    collection, names = fake_chroma
    kb = KnowledgeBase()
    expected = dict(collection.records)
    assert len({meta["doc_id"] for _, meta in expected.values()}) == 24
    assert all(meta["source"].startswith(("knowledge/", "https://")) for _, meta in expected.values())
    assert not any("套餐升级" in text or meta["title"] in {"退款政策", "账户管理"} for text, meta in expected.values())
    KnowledgeBase()
    assert collection.records == expected
    collection.records.pop(next(iter(collection.records)))
    KnowledgeBase()
    assert collection.records == expected
    assert set(names) == {"medipet_knowledge"}
    results = kb.search("身份证明", 3)
    assert results and all({"chunk_id", "doc_id", "source", "source_id", "title"} <= row.keys() for row in results)
    assert all(row["chunk_id"].startswith(row["doc_id"] + ":") for row in results)
    assert kb.search("从未存在的知识主题") == []


def test_legacy_upload_and_stable_ids(fake_chroma):
    kb = KnowledgeBase()
    document = {"title": "上传材料", "content": "带好就诊材料。"}
    assert kb.add_documents([document, document]) == 1
    assert kb.add_documents([document]) == 0
    result = kb.search("带好就诊材料")[0]
    assert result["source"].startswith("upload:upload-")
    with pytest.raises(ValueError, match="冲突"):
        kb.add_documents([{"doc_id": "same", "content": "甲"}, {"doc_id": "same", "content": "乙"}])
    assert kb.add_documents([{"title": "空白", "content": " "}]) == 0


def test_default_knowledge_upgrade_replaces_old_chunks_preserves_uploads(fake_chroma, tmp_path):
    collection, _ = fake_chroma
    path = tmp_path / "guide.md"
    header = "---\ndoc_id: guide\ntitle: 导引\nsource_id: guide-source\nsource: knowledge/guide.md\n---\n"
    path.write_text(header + "旧内容。" * 200, encoding="utf-8")
    kb = KnowledgeBase(knowledge_dir=str(tmp_path))
    assert len(collection.records) > 1
    kb.add_documents([{"title": "用户材料", "content": "保留上传内容"}])
    upload = {k: v for k, v in collection.records.items() if k.startswith("upload-")}
    path.write_text(header + "允许有来源的信息参考，不做诊断。", encoding="utf-8")
    KnowledgeBase(knowledge_dir=str(tmp_path))
    assert set(collection.records) == {"guide:0", *upload}
    assert collection.records["guide:0"][0] == "允许有来源的信息参考，不做诊断。"
    assert all(collection.records[key] == record for key, record in upload.items())
    upserts = collection.upserts
    KnowledgeBase(knowledge_dir=str(tmp_path))
    assert collection.upserts == upserts


def test_dynamic_questions_only_retrieve_static_process_boundary(fake_chroma):
    kb = KnowledgeBase()
    result = kb.search("没有号源时")[0]
    assert result["doc_id"] == "appointment-howto"
    assert "不能根据历史文字声称仍有号" in result["content"]
    assert not {"remaining", "appointment_id", "patient_id", "slot_id"} & result.keys()


@pytest.mark.parametrize("kind", ["empty", "no_frontmatter", "missing_source", "duplicate"])
def test_bad_knowledge_directory_is_not_silently_accepted(fake_chroma, tmp_path, kind):
    text = "---\ndoc_id: sample\ntitle: 标题\nsource_id: source\nsource: sample.md\n---\n医院材料"
    if kind == "no_frontmatter":
        text = "医院材料"
    elif kind == "missing_source":
        text = text.replace("source: sample.md\n", "")
    if kind != "empty":
        (tmp_path / "one.md").write_text(text, encoding="utf-8")
    if kind == "duplicate":
        (tmp_path / "two.md").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        KnowledgeBase(knowledge_dir=str(tmp_path))


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_knowledge_top_k_validation(fake_chroma, top_k):
    with pytest.raises(ValueError):
        KnowledgeBase().search("材料", top_k)


def test_knowledge_async_wrappers_preserve_sources(fake_chroma):
    async def run():
        kb = KnowledgeBase()
        assert await kb.add_documents_async([{"title": "异步上传", "content": "异步检索材料"}]) == 1
        assert (await kb.search_handler({"query": "异步检索材料"}, None))[0]["source"]
        assert await kb.doc_count_async() == kb.doc_count
    asyncio.run(run())


def document(key, score=0.9):
    return {"chunk_id": key, "doc_id": key.split(":")[0], "title": key, "content": "医院材料 " + key,
            "source_id": key, "source": "knowledge/" + key + ".md", "score": score}


@pytest.fixture
def make_manager(monkeypatch):
    def make(responses=()):
        pending = list(responses)
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            result = pending.pop(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(result))])

        monkeypatch.setattr(tm_module, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create)))
        manager = MCPToolManager("fake-key")
        manager.model_calls = calls
        return manager
    return make


def register(manager, handler, **kwargs):
    tool = Tool("knowledge_search", "医院知识", handler,
                {"required": ["query"], "properties": {"query": {"type": "string"}}}, **kwargs)
    manager.register(tool)
    return tool


def test_rewrite_preserves_original_and_limits_unique_additions(make_manager):
    manager = make_manager([["材料", "材料", "", " 到院 ", "挂号", "额外查询"]])
    assert asyncio.run(manager.rewrite_query("原问题", n=2)) == (["原问题", "材料", "到院"], None)
    assert asyncio.run(manager.rewrite_query("原问题", n=0)) == (["原问题"], None)
    assert len(manager.model_calls) == 1


@pytest.mark.parametrize("response", [["材料", 9], {"query": "材料"}, RuntimeError("模型不可用")])
def test_rewrite_failure_preserves_original_and_error(make_manager, response):
    manager = make_manager([response])
    queries, error = asyncio.run(manager.rewrite_query("原问题"))
    assert queries == ["原问题"] and error


def test_parallel_recall_deduplicates_identity_and_preserves_sources(make_manager):
    async def run():
        manager = make_manager([["子查询"], [1, 0]])
        entered = set()
        both_entered = asyncio.Event()

        async def handler(params, context):
            entered.add(params["query"])
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), 1)
            return [document("a:0", 0.9 if params["query"] == "原查询" else 0.5), document("b:0")]

        register(manager, handler)
        result = await manager.search_with_rewrite("knowledge_search", "原查询", 1)
        assert result.success and result.reranked and not result.recall_errors
        assert [row["chunk_id"] for row in result.data] == ["b:0"]
        assert result.data[0]["source"] == "knowledge/b:0.md"
        assert entered == {"原查询", "子查询"}
    asyncio.run(run())


def test_legacy_content_dedup_ignores_scores(make_manager):
    async def run():
        manager = make_manager([[]])
        async def handler(params, context):
            return [{"content": "same", "score": 1}, {"content": "same", "score": 0.2}]
        register(manager, handler)
        result = await manager.search_with_rewrite("knowledge_search", "材料", 1)
        assert result.success and len(result.data) == 1 and not result.reranked
        assert len(manager.model_calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("mode,code", [("empty", "no_results"), ("failure", "retrieval_failed"), ("bad_payload", "retrieval_failed")])
def test_no_results_and_failed_retrieval_remain_distinct(make_manager, mode, code):
    async def run():
        manager = make_manager([[]])
        async def handler(params, context):
            if mode == "failure":
                raise RuntimeError("存储故障")
            return [] if mode == "empty" else {"not": "documents"}
        register(manager, handler, fallback=lambda *args: [document("fabricated")])
        result = await manager.search_with_rewrite("knowledge_search", "材料")
        assert not result.success and result.data == [] and result.error_code == code
        assert bool(result.recall_errors) == (mode != "empty")
    asyncio.run(run())


def test_partial_failure_and_rewrite_failure_return_real_data(make_manager):
    async def run():
        manager = make_manager([["失败查询"], RuntimeError("改写故障")])
        async def handler(params, context):
            if params["query"] == "失败查询":
                raise RuntimeError("该路召回失败")
            return [document("real")]
        register(manager, handler)
        partial = await manager.search_with_rewrite("knowledge_search", "材料")
        assert partial.success and partial.partial and partial.recall_errors
        recovered = await manager.search_with_rewrite("knowledge_search", "材料")
        assert recovered.success and recovered.rewrite_error == "改写故障"
        assert not recovered.partial and not recovered.recall_errors
        assert recovered.data == [document("real")]
    asyncio.run(run())


@pytest.mark.parametrize("order", [[0, 0], [True, 0], [0, 2], [0], ["1", 0], RuntimeError("重排服务故障")])
def test_invalid_rerank_falls_back_with_failure(make_manager, order):
    manager = make_manager([order])
    items = [document("a"), document("b")]
    data, applied, error = asyncio.run(manager._rerank("材料", items, 1))
    assert data == items[:1] and not applied and error


def test_cache_preserves_applied_rerank_and_does_not_cache_failed_rerank(make_manager):
    async def run():
        manager = make_manager([RuntimeError("temporary"), [1, 0]])
        calls = []
        async def handler(params, context):
            calls.append(params)
            return [document("a"), document("b")]
        register(manager, handler, cache_ttl=60, supports_rerank=True)
        first = await manager.call("knowledge_search", {"query": "材料"}, rerank_top_k=1)
        assert first.rerank_error and not first.reranked
        second = await manager.call("knowledge_search", {"query": "材料"}, rerank_top_k=1)
        third = await manager.call("knowledge_search", {"query": "材料"}, rerank_top_k=1)
        assert second.reranked and third.cached and third.reranked and not third.rerank_error
        assert third.data == [document("b")] and len(calls) == 2
    asyncio.run(run())


def test_timeout_fallback_and_circuit_recovery(make_manager):
    async def run():
        manager = make_manager()
        calls = []
        async def handler(params, context):
            calls.append(params)
            await asyncio.Event().wait()
        tool = register(manager, handler, timeout_s=0.01, fallback=lambda *args: "请稍后重试")
        tool.breaker.threshold = 1
        first = await manager.call("knowledge_search", {"query": "材料"})
        assert not first.success and first.fallback_used and first.error == "执行超时"
        second = await manager.call("knowledge_search", {"query": "材料"})
        assert not second.success and "熔断" in second.error and len(calls) == 1
        tool.breaker.recovery_s = 0
        async def recovered(params, context):
            return [document("real")]
        tool.handler = recovered
        third = await manager.call("knowledge_search", {"query": "材料"})
        assert third.success and tool.breaker.state is CircuitState.CLOSED
    asyncio.run(run())


def test_concurrent_requests_do_not_share_failure_status(make_manager):
    async def run():
        manager = make_manager()
        both_entered = asyncio.Event()
        entered = set()
        async def create(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            is_bad = '"失败改写"' in prompt
            entered.add(is_bad)
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), 1)
            if is_bad:
                raise RuntimeError("仅本轮改写失败")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="[]")])
        manager._client.messages.create = create
        async def handler(params, context):
            return [document(params["query"])]
        register(manager, handler)
        failed, normal = await asyncio.gather(manager.search_with_rewrite("knowledge_search", "失败改写"),
                                               manager.search_with_rewrite("knowledge_search", "正常改写"))
        assert failed.success and failed.rewrite_error
        assert normal.success and normal.rewrite_error is None and not normal.reranked
    asyncio.run(run())
