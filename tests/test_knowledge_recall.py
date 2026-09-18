"""检索回归：重放实测向量候选，不调用模型或现有 Chroma 服务。"""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from mcp import knowledge_base as kb_module
from mcp.knowledge_base import KnowledgeBase
from mcp.knowledge_retrieval import lexical_terms


class ReplayCollection:
    def __init__(self):
        self.records = {}
        self.vector_ids = []
        self.query_sizes = []

    def count(self):
        return len(self.records)

    def get(self, ids=None, include=(), where=None):
        keys = [key for key in (ids if ids is not None else self.records) if key in self.records]
        if where:
            keys = [key for key in keys if all(self.records[key][1].get(k) == v for k, v in where.items())]
        return {"ids": keys, "documents": [self.records[key][0] for key in keys],
                "metadatas": [self.records[key][1] for key in keys]}

    def upsert(self, ids, documents, metadatas):
        self.records.update(zip(ids, zip(documents, metadatas)))

    add = upsert

    def delete(self, ids):
        for key in ids:
            self.records.pop(key, None)

    def query(self, query_texts, n_results):
        self.query_sizes.append(n_results)
        keys = self.vector_ids[:n_results]
        rows = self.get(ids=keys)
        return {key: [value] for key, value in rows.items()} | {"distances": [[0.8 + i * 0.01 for i in range(len(keys))]]}


@pytest.fixture
def replay_kb(monkeypatch):
    collection = ReplayCollection()
    client = SimpleNamespace(heartbeat=lambda: None, get_or_create_collection=lambda **_: collection)
    monkeypatch.setattr(kb_module.chromadb, "HttpClient", lambda **_: client)
    return KnowledgeBase(), collection


@pytest.mark.parametrize("query,expected,vector_ids", [
    # 2026-09-18 实测 retrieval-06 / 08 / 23 原始 top-3。
    ("取消预约时需要先选记录吗，取消后号源会释放吗？", "appointment-cancellation",
     ["department-pediatrics:0", "first-visit-materials:0", "arrival-checkin:0"]),
    ("有人推轮椅，院内无障碍路线支持哪些地点和设施？", "accessible-wayfinding",
     ["first-visit-materials:0", "human-guidance:0", "arrival-checkin:0"]),
    ("布洛芬200毫克普通片资料为什么不能套到儿童混悬液？", "health-ibuprofen-label",
     ["first-visit-materials:0", "human-guidance:0", "general-preparation:0"]),
])
def test_observed_chinese_vector_misses_are_recalled(replay_kb, query, expected, vector_ids):
    kb, collection = replay_kb
    collection.vector_ids = vector_ids
    results = kb.search(query, top_k=3)
    assert expected in {item["doc_id"] for item in results}
    assert len(results) <= 3


@pytest.mark.parametrize("query,title,content", [
    ("图书归还以后借阅押金如何退还", "图书借阅押金退还", "图书归还时请提供借阅凭证，柜台核对后退还押金。"),
    ("XYLOPHONE resonance calibration", "Instrument manual", "Xylophone resonance calibration requires the supplied tuning reference."),
])
def test_new_uploads_without_predefined_ids_are_lexically_recalled(replay_kb, query, title, content):
    kb, collection = replay_kb
    collection.vector_ids = ["first-visit-materials:0", "department-pediatrics:0", "arrival-checkin:0"]
    assert not any(row["source"].startswith("upload:") for row in kb.search(query, 3))
    kb.add_documents([{"title": title, "content": content}])
    results = kb.search(query, 3)
    assert any(row["content"] == content and row["source"].startswith("upload:") for row in results)
    assert len(results) <= 3 and len({row["chunk_id"] for row in results}) == len(results)
    assert all(row["score_type"] == "hybrid_rrf" for row in results)


def test_current_collection_text_is_used_after_same_count_update(replay_kb):
    kb, collection = replay_kb
    collection.vector_ids = ["first-visit-materials:0"]
    kb.add_documents([{"doc_id": "custom", "title": "旧标题", "content": "旧办理内容"}])
    kb.search("新图书借阅规则", 3)
    count = kb.doc_count
    kb._sync_default_documents([{"doc_id": "custom", "title": "图书借阅规则", "content": "图书借阅柜台办理。",
                                 "source_id": "custom-source", "source": "custom.md"}])
    assert kb.doc_count == count
    assert any(row["doc_id"] == "custom" and row["source"] == "custom.md" for row in kb.search("新图书借阅规则", 3))


def test_semantic_only_match_and_empty_result_keep_existing_semantics(replay_kb):
    kb, collection = replay_kb
    collection.vector_ids = ["appointment-howto:0", "first-visit-materials:0"]
    results = kb.search("semanticparaphrase", 1)
    assert [row["doc_id"] for row in results] == ["appointment-howto"]
    assert results[0]["score"] == pytest.approx(0.2)
    collection.vector_ids = []
    assert kb.search("预约", 3) == []


def test_vector_errors_are_not_hidden_as_success(replay_kb, monkeypatch):
    kb, collection = replay_kb
    def unavailable(**_):
        raise RuntimeError("vector unavailable")
    monkeypatch.setattr(collection, "query", unavailable)
    with pytest.raises(RuntimeError, match="vector unavailable"):
        kb.search("取消预约", 3)


def test_tokenization_respects_word_boundaries_and_width():
    assert lexical_terms("ＡＢＣ ２００mg；无障碍，路线") == ["abc", "200mg", "无障", "障碍", "路线"]


def test_parallel_queries_return_independent_bounded_candidates(replay_kb):
    kb, collection = replay_kb
    collection.vector_ids = ["first-visit-materials:0", "department-pediatrics:0", "arrival-checkin:0"]
    async def run():
        return await asyncio.gather(kb.search_async("取消预约释放号源", 3),
                                    kb.search_async("布洛芬普通片混悬液", 3))
    cancel, medicine = asyncio.run(run())
    assert "appointment-cancellation" in {row["doc_id"] for row in cancel}
    assert "health-ibuprofen-label" in {row["doc_id"] for row in medicine}
    assert all(size == 6 for size in collection.query_sizes)
    assert len(cancel) <= 3 and len(medicine) <= 3


@pytest.mark.skipif(os.getenv("MEDIPET_TEST_LOCAL_RETRIEVAL") != "1", reason="需显式启用本地 Chroma/缓存 embedding 探针")
def test_real_ephemeral_chroma_recalls_chinese_topics_without_migrating_collection():
    import chromadb
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    assert (ONNXMiniLM_L6_V2.DOWNLOAD_PATH / "onnx/model.onnx").is_file(), "必须预先存在模型缓存，不触发下载"
    client = chromadb.EphemeralClient(settings=chromadb.Settings(anonymized_telemetry=False))
    name = "test_recall_" + uuid.uuid4().hex
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._collection = client.create_collection(name)
    kb._knowledge_dir = Path(__file__).resolve().parents[1] / "knowledge"
    try:
        kb._load_default_docs()
        for query, expected in [("取消预约释放号源", "appointment-cancellation"),
                                ("轮椅无障碍路线", "accessible-wayfinding"),
                                ("布洛芬普通片混悬液", "health-ibuprofen-label")]:
            assert expected in {row["doc_id"] for row in kb.search(query, 3)}
        assert kb.doc_count == 24
    finally:
        client.delete_collection(name)
