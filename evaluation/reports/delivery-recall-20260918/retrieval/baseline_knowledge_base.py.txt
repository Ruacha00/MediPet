"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import chromadb

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 客户端默认使用 all-MiniLM-L6-v2 计算文档和查询向量；
    HTTP 与本地模式均由客户端执行 embedding，存储端负责向量检索。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "medipet_knowledge"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
        knowledge_dir: Optional[str] = None,
    ):
        self._knowledge_dir = Path(knowledge_dir) if knowledge_dir is not None else Path(__file__).resolve().parents[1] / "knowledge"
        # 优先连接独立 ChromaDB 服务；两种模式都需要客户端的 embedding 模型。
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 使用默认客户端 embedding_function；首次使用可能触发模型下载。
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "MediPet RAG 知识库"},
        )

        # 同步内置文档的内容变更，保留用户上传与其他集合。
        self._load_default_docs()

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 可带 doc_id/source_id/source；原 title/content 上传格式继续支持。
        长文档会自动切片（每片 500 字）。
        """
        records = {}

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            doc_id = doc.get("doc_id") or "upload-" + hashlib.sha256(f"{title}\0{content}".encode()).hexdigest()
            source_id = doc.get("source_id") or doc_id
            source = doc.get("source") or f"upload:{doc_id}"
            chunks  = self._chunk_text(content, chunk_size=500)

            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}:{i}"
                record = (chunk, {
                    "doc_id": doc_id, "title": title, "source_id": source_id, "source": source,
                    "chunk_index": i, "total_chunks": len(chunks),
                })
                if chunk_id in records and records[chunk_id] != record:
                    raise ValueError(f"同批文档片段 ID 冲突: {chunk_id}")
                records[chunk_id] = record

        if not records:
            return 0
        existing = set(self._collection.get(ids=list(records), include=[])["ids"])
        ids = [chunk_id for chunk_id in records if chunk_id not in existing]
        if ids:
            self._collection.add(
                ids=ids, documents=[records[key][0] for key in ids], metadatas=[records[key][1] for key in ids],
            )
            logger.info(f"知识库补充 {len(ids)} 个文档片段")

        return len(ids)

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """异步导入文档；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.add_documents, documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        客户端将 query 转为向量，ChromaDB 按集合距离度量匹配。
        """
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k 必须是正整数")
        count = self._collection.count()
        if not query.strip() or count == 0:
            return []
        results = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, count),
        )

        items = []
        if results["documents"] and results["documents"][0]:
            for chunk_id, doc, meta, dist in zip(
                results["ids"][0],
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "chunk_id": chunk_id,
                    "doc_id":   meta.get("doc_id", ""),
                    "title":    meta.get("title", ""),
                    "source_id": meta.get("source_id", ""),
                    "source":   meta.get("source", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),
                })

        return items

    async def search_async(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """异步检索；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """异步获取文档片段数量。"""
        return await asyncio.to_thread(self._collection.count)

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return await self.search_async(query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """同步带固定来源的内置 Markdown 文档，升级时也替换旧内容。"""
        documents = []
        doc_ids = set()
        for path in sorted(self._knowledge_dir.glob("*.md")):
            match = re.fullmatch(r"---[^\S\n]*\n(.*?)\n---[^\S\n]*(?:\n|$)(.*)", path.read_text(encoding="utf-8"), re.S)
            if not match:
                raise ValueError(f"知识文档缺少 frontmatter: {path.name}")
            metadata = {}
            for line in match.group(1).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip().strip("\"'")
            if any(not metadata.get(key) for key in ("doc_id", "title", "source_id", "source")):
                raise ValueError(f"知识文档来源字段不完整: {path.name}")
            if metadata["doc_id"] in doc_ids:
                raise ValueError(f"重复知识文档 ID: {metadata['doc_id']}")
            doc_ids.add(metadata["doc_id"])
            documents.append({**metadata, "content": match.group(2).strip()})
        if not documents:
            raise ValueError("未找到医院知识文档")
        changed = self._sync_default_documents(documents)
        logger.info("医院知识初始化: %s 篇文档，更新 %s 个片段", len(documents), changed)

    def _sync_default_documents(self, documents: List[Dict[str, str]]) -> int:
        """仅同步本次内置文档 ID；不重新嵌入未变化内容，不清空集合。"""
        changed = 0
        for doc in documents:
            doc_id = doc["doc_id"]
            chunks = self._chunk_text(doc["content"], chunk_size=500)
            records = {
                f"{doc_id}:{i}": (chunk, {
                    "doc_id": doc_id, "title": doc["title"],
                    "source_id": doc["source_id"], "source": doc["source"],
                    "chunk_index": i, "total_chunks": len(chunks),
                })
                for i, chunk in enumerate(chunks)
            }
            previous = self._collection.get(
                where={"doc_id": doc_id}, include=["documents", "metadatas"],
            )
            existing = dict(zip(previous["ids"], zip(previous["documents"], previous["metadatas"])))
            updated = [key for key, record in records.items() if existing.get(key) != record]
            if updated:
                self._collection.upsert(
                    ids=updated, documents=[records[key][0] for key in updated],
                    metadatas=[records[key][1] for key in updated],
                )
            obsolete = sorted(set(existing) - set(records))
            if obsolete:
                self._collection.delete(ids=obsolete)
            changed += len(updated) + len(obsolete)
        return changed
