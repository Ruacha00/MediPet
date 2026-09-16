"""
亮点：多轮对话记忆管理

三级记忆架构，模拟人类记忆机制：
  1. 工作记忆（Redis）—— 当前会话的最近 N 条消息，毫秒级读写
  2. 情景记忆（ChromaDB）—— 跨会话的历史对话，按语义相似度检索
  3. 用户画像（ChromaDB）—— 从对话中提炼的长期偏好和实体

关键设计：
  - 上下文构建时三级记忆融合，按重要性 + 时效性排序
  - 工作记忆超过阈值时自动压缩（LLM 摘要），防止 context 爆炸
  - Chroma 默认 embedding 在客户端执行，HTTP 与本地模式相同
"""
import hashlib
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import chromadb
import redis.asyncio as redis
from redis.exceptions import WatchError
from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content, llm_request_options
from hospital.models import VisitIdentity
from memory.visit_store import VisitStore

logger = logging.getLogger(__name__)


class MsgRole(Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"


@dataclass
class Message:
    role:       MsgRole
    content:    str
    timestamp:  datetime = field(default_factory=datetime.now)
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    """传给 Agent 的完整上下文。"""
    recent_messages:  List[Message]   # 工作记忆：最近对话
    relevant_history: List[str]       # 情景记忆：语义相关的历史片段
    user_profile:     Dict[str, Any]  # 用户画像：偏好、常用实体
    summary:          str             # 当前会话摘要（压缩后）
    selection_context: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _clean(text: str) -> str:
        """移除 Unicode 代理字符，防止编码错误。"""
        return text.encode("utf-8", errors="ignore").decode("utf-8")

    def to_prompt_text(self) -> str:
        """将记忆上下文格式化为 LLM 可用的文本。"""
        parts = []
        if self.summary:
            parts.append(f"[会话摘要]\n{self._clean(self.summary)}")
        if self.relevant_history:
            parts.append("[相关历史；业务状态均是当时快照，当前状态须查询工具]\n" + "\n".join(f"- {self._clean(h)}" for h in self.relevant_history[:3]))
        if self.user_profile:
            parts.append(f"[用户画像]\n{json.dumps(self.user_profile, ensure_ascii=True)}")
        if self.recent_messages:
            parts.append("[最近对话]")
            for m in self.recent_messages[-8:]:
                parts.append(f"{m.role.value}: {self._clean(m.content)}")
        if self.selection_context:
            parts.append("[当前事项的最近查询与选择；序号从1开始，仅可引用列出的真实号源ID；余量和方案可执行性须由业务工具复核]\n"
                         + json.dumps(self.selection_context, ensure_ascii=False))
        return "\n\n".join(parts)


class MemoryManager:
    """
    三级记忆管理器。

    工作记忆存 Redis（TTL 24h），情景记忆和用户画像存 ChromaDB（持久化）。
    """

    WORKING_MAX   = 20    # 工作记忆最大条数，超过则触发压缩
    COMPRESS_AT   = 15    # 达到此条数时压缩，保留摘要 + 最近 5 条
    HISTORY_TOP_K = 5     # 情景记忆检索返回条数
    SUMMARY_MAX_CHARS = 800
    PROFILE_DOC_PREFIX = "user_profile:"

    def __init__(
        self,
        redis_url:    str = "redis://localhost:6379/0",
        chroma_host:  str = "localhost",
        chroma_port:  int = 8000,
        chroma_path:  str = "./data/chroma",
        api_key:      str = "",
        base_url:     Optional[str] = None,
        model:        str = "claude-3-5-sonnet-20241022",
        redis_client=None,
        chroma_client=None,
        visit_store: VisitStore | None = None,
        collection_prefix: str = "medipet_",
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._model  = model

        self._redis = redis_client if redis_client is not None else redis.from_url(redis_url, decode_responses=True)
        self._owns_redis = redis_client is None
        self._visits = visit_store
        self._prefix = visit_store.prefix if visit_store else "medipet:"

        # ChromaDB：优先连接独立服务（docker compose 模式），连不上则降级为本地嵌入式
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            chroma = chroma_client or chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            chroma.heartbeat()  # 测试连接
            logger.info(f"ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"ChromaDB 服务不可用，使用本地嵌入式模式: {chroma_path}")
            chroma = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 情景记忆：存储历史对话片段
        self._episodic = chroma.get_or_create_collection(f"{collection_prefix}episodic")
        # 用户画像：存储提炼出的偏好和实体
        self._profile  = chroma.get_or_create_collection(f"{collection_prefix}profiles")

    # ── 写入 ──────────────────────────────────────────────────────────────────

    async def add_message(
        self,
        identity: VisitIdentity,
        role:    MsgRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        compress: bool = True,
    ) -> None:
        """写入工作记忆并续期；默认超阈值压缩，急症入口可关闭本次压缩。"""
        identity = VisitIdentity.model_validate(identity)
        clean_metadata = {
            self._safe_text(k): self._safe_metadata_value(v)
            for k, v in (metadata or {}).items()
        }
        msg = Message(role=role, content=self._safe_text(content), metadata=clean_metadata)
        key = self._wm_key(identity)

        # 追加到 Redis 列表（左推，最新在前）
        await self._redis.lpush(key, json.dumps({
            "role":      msg.role.value,
            "content":   msg.content,
            "ts":        msg.timestamp.isoformat(),
            "metadata":  msg.metadata,
        }))
        await self._redis.expire(key, 86400)  # 24h TTL

        # 超过压缩阈值时触发压缩
        if compress and await self._redis.llen(key) >= self.COMPRESS_AT:
            await self._compress(identity)

    async def invalidate_working_memory(self, identity: VisitIdentity) -> None:
        """完整操作历史落盘后，仅失效本事项窗口；下一次普通读取沿原路径恢复。"""
        identity = VisitIdentity.model_validate(identity)
        await self._redis.delete(self._wm_key(identity))

    async def update_profile(self, identity: VisitIdentity) -> None:
        """
        从当前工作记忆中提炼用户偏好，更新用户画像。
        用 LLM 提炼偏好，然后存入 ChromaDB（ChromaDB 内置 embedding，不依赖外部 API）。
        """
        identity = VisitIdentity.model_validate(identity)
        messages = await self._get_working_memory(identity)
        if not messages:
            return

        current_profile = await self._get_profile(identity)

        user_messages = [m.content for m in messages[-10:] if m.role == MsgRole.USER]
        text = self._safe_text("\n".join(user_messages))
        profile_ctx = json.dumps(current_profile, ensure_ascii=False) if current_profile else "{}"
        prompt = f"""只提取用户明确表达的当前就诊人资料和参与者表达偏好，返回 JSON。
patient_facts 为当前患者明确资料；preferences 仅为语言、回答长短等参与者表达偏好。
每项必须逐字引用用户原句，不推断病情、不使用助手答复，不把其他患者的信息写入当前患者。
没有明确资料则返回空列表。不要把预约、剩余号源或时间敏感的操作状态保存为画像事实。
对话:
{text}

已有画像:
{profile_ctx}

返回格式: {{"patient_facts": ["用户原句"], "preferences": ["用户原句"]}}"""
        prompt = self._safe_text(prompt)

        try:
            resp = await self._client.messages.create(
                **llm_request_options(),
                model=self._model, max_tokens=512, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            profile_data = json.loads(raw[s:e])

            for field_name, scope in (("patient_facts", "patient"), ("preferences", "participant")):
                proposed = profile_data.get(field_name, []) if isinstance(profile_data, dict) else []
                # 只存原始明确表达；模型合成的疾病/身份断言没有逐字证据时不能落库。
                explicit = [value for value in proposed if isinstance(value, str) and value.strip() and any(value in message for message in user_messages)] if isinstance(proposed, list) else []
                existing = current_profile.get(field_name, [])
                values = self._dedupe_texts(existing + explicit)[-20:]
                if not values:
                    continue
                doc_id = self._profile_doc_id(identity, scope)
                await asyncio.to_thread(self._profile.upsert, ids=[doc_id],
                    documents=[json.dumps({field_name: values}, ensure_ascii=False)],
                    metadatas=[{"user_id": identity.user_id, "patient_id": identity.patient_id if scope == "patient" else "",
                                "conv_id": identity.conv_id, "scope": scope, "updated_at": datetime.now().isoformat()}])
            logger.info("就诊资料与表达偏好已更新")
        except Exception as ex:
            logger.warning(f"更新用户画像失败: {ex}")

    # ── 读取 ──────────────────────────────────────────────────────────────────

    async def get_context(self, identity: VisitIdentity, query: str = "") -> MemoryContext:
        """
        构建完整的记忆上下文。

        query 用于从情景记忆中检索语义相关的历史片段。
        """
        # 1. 工作记忆（当前会话最近消息）
        identity = VisitIdentity.model_validate(identity)
        query = self._safe_text(query)

        if self._visits:
            await self._visits.require_identity(identity.user_id, identity.conv_id, patient_id=identity.patient_id)

        recent = await self._get_working_memory(identity)
        if not recent and self._visits:
            recent = await self._restore_working_memory(identity)

        # 2. 情景记忆（跨会话语义检索）
        history = await self._search_episodic(
            identity,
            query or (recent[-1].content if recent else ""),
        )

        # 3. 用户画像
        profile = await self._get_profile(identity)

        # 4. 会话摘要（如果已压缩过）
        summary = await self._redis.get(self._summary_key(identity)) or ""
        selection = (await self._visits.get_selection(identity.user_id, identity.conv_id)).model_dump(mode="json") if self._visits else {}

        return MemoryContext(
            recent_messages=recent,
            relevant_history=history,
            user_profile=profile,
            summary=summary,
            selection_context=selection,
        )

    async def _restore_working_memory(self, identity: VisitIdentity) -> List[Message]:
        """只恢复当前事项窗口；完整历史与业务卡片保持原样，不重放操作。"""
        history = await self._visits.get_messages(identity.user_id, identity.conv_id)
        if not history:
            return []
        key = self._wm_key(identity)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if await pipe.lrange(key, 0, 0):
                    return await self._get_working_memory(identity)
                pipe.multi()
                for message in history[-self.WORKING_MAX:]:
                    metadata = {**message.metadata, "message_id": message.message_id,
                                "artifacts": [artifact.model_dump(mode="json") for artifact in message.artifacts]}
                    pipe.lpush(key, json.dumps({"role": message.role, "content": message.content,
                                              "ts": message.created_at.isoformat(), "metadata": metadata}))
                pipe.expire(key, 86400)
                await pipe.execute()
        except WatchError:
            # 并发请求已经恢复或写入该窗口，读取其结果；不循环覆盖。
            return await self._get_working_memory(identity)
        if len(history) >= self.COMPRESS_AT:
            await self._compress(identity)
        return await self._get_working_memory(identity)

    # ── 压缩（防止 context 爆炸）─────────────────────────────────────────────

    async def _compress(self, identity: VisitIdentity) -> None:
        """
        工作记忆压缩：
          1. 用 LLM 对旧消息生成摘要
          2. 摘要存 Redis（覆盖旧摘要）
          3. 旧消息存入情景记忆（ChromaDB）供跨会话检索
          4. 工作记忆只保留最近 5 条
        """
        messages = await self._get_working_memory(identity)
        if len(messages) < self.COMPRESS_AT:
            return

        to_compress = messages[:-5]   # 保留最近 5 条
        keep        = messages[-5:]

        # LLM 摘要
        text = self._safe_text("\n".join(f"{m.role.value}: {m.content}" for m in to_compress))
        prompt = self._safe_text(f"用 2-3 句话总结以下对话的关键信息：\n{text}")
        try:
            resp = await self._client.messages.create(
                **llm_request_options(),
                model=self._model, max_tokens=256, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = self._safe_text(extract_text_content(resp.content)).strip()
        except Exception:
            summary = f"对话包含 {len(to_compress)} 条消息（摘要生成失败）"

        # 存摘要到 Redis
        skey = self._summary_key(identity)
        old_summary = await self._redis.get(skey) or ""
        new_summary = await self._merge_summary(old_summary, summary)
        await self._redis.setex(skey, 86400, new_summary)

        # 旧消息存入情景记忆
        await self._store_episodic(identity, text, summary)

        # 重置工作记忆为最近 5 条
        key = self._wm_key(identity)
        await self._redis.delete(key)
        for m in keep:
            await self._redis.lpush(key, json.dumps({
                "role": m.role.value, "content": m.content,
                "ts": m.timestamp.isoformat(), "metadata": m.metadata,
            }))
        await self._redis.expire(key, 86400)
        logger.info(f"工作记忆压缩完成: {identity.patient_id}/{identity.conv_id}，摘要 {len(summary)} 字")

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    async def _get_working_memory(self, identity: VisitIdentity) -> List[Message]:
        key  = self._wm_key(identity)
        raws = await self._redis.lrange(key, 0, self.WORKING_MAX - 1)
        msgs = []
        for raw in reversed(raws):  # Redis lpush 最新在前，reversed 还原时序
            d = json.loads(raw)
            msgs.append(Message(
                role=MsgRole(d["role"]),
                content=d["content"],
                timestamp=datetime.fromisoformat(d["ts"]),
                metadata=d.get("metadata", {}),
            ))
        return msgs

    async def _search_episodic(self, identity: VisitIdentity, query: str) -> List[str]:
        """语义检索情景记忆。ChromaDB 内置 embedding，不依赖外部 API。"""
        query_text = self._safe_text(query).strip()
        if not query_text:
            return []
        try:
            results = await self._query_episodic(
                query_text,
                n_results=self.HISTORY_TOP_K,
                where={"$and": [{"user_id": identity.user_id}, {"patient_id": identity.patient_id}, {"conv_id": identity.conv_id}]},
            )
            docs = self._extract_docs(results)
            if len(docs) < self.HISTORY_TOP_K:
                fallback = await self._query_episodic(
                    query_text,
                    n_results=self.HISTORY_TOP_K,
                    where={"$and": [{"user_id": identity.user_id}, {"patient_id": identity.patient_id}, {"conv_id": {"$ne": identity.conv_id}}]},
                )
                docs.extend(self._extract_docs(fallback))
            return self._dedupe_texts(docs)[: self.HISTORY_TOP_K]
        except Exception as ex:
            logger.warning(f"情景记忆检索失败: {ex}")
            return []

    async def _store_episodic(self, identity: VisitIdentity, text: str, summary: str) -> None:
        """将压缩后的对话片段存入情景记忆。ChromaDB 内置 embedding，不依赖外部 API。"""
        try:
            text = self._safe_text(text)
            summary = self._safe_text(summary)
            doc_id = hashlib.md5(f"{identity.user_id}:{identity.patient_id}:{identity.conv_id}:{time.time()}".encode()).hexdigest()
            # 直接传 documents，ChromaDB 内置模型自动生成 embedding
            await asyncio.to_thread(
                self._episodic.add,
                ids=[doc_id],
                documents=[summary],
                metadatas=[{"user_id": identity.user_id, "patient_id": identity.patient_id, "conv_id": identity.conv_id,
                            "ts": datetime.now().isoformat(), "full_text": self._safe_text(text[:500])}],
            )
        except Exception as ex:
            logger.warning(f"存储情景记忆失败: {ex}")

    async def _get_profile(self, identity: VisitIdentity) -> Dict[str, Any]:
        """患者资料与参与者偏好分开保存、分别按稳定身份读取。"""
        try:
            profile = {}
            for scope in ("patient", "participant"):
                direct = await asyncio.to_thread(self._profile.get, ids=[self._profile_doc_id(identity, scope)])
                if direct.get("documents"):
                    profile.update(json.loads(direct["documents"][0]))
            return profile
        except Exception:
            pass
        return {}

    async def close(self) -> None:
        """关闭异步 Redis 连接。"""
        if self._owns_redis:
            await self._redis.aclose()

    def _wm_key(self, identity: VisitIdentity) -> str:
        return f"{self._prefix}wm:{identity.user_id}:{identity.patient_id}:{identity.conv_id}"

    def _summary_key(self, identity: VisitIdentity) -> str:
        return f"{self._prefix}summary:{identity.user_id}:{identity.patient_id}:{identity.conv_id}"

    @classmethod
    def _profile_doc_id(cls, identity: VisitIdentity, scope: str = "patient") -> str:
        patient = f":{identity.patient_id}" if scope == "patient" else ""
        return f"{scope}:{identity.user_id}{patient}"

    @staticmethod
    def _safe_text(value: Any) -> str:
        """转成 ChromaDB 可接受的普通 UTF-8 字符串。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @classmethod
    def _safe_metadata_value(cls, value: Any) -> Any:
        """递归清洗 metadata，避免 Redis/ChromaDB 后续读写遇到非法 UTF-8。"""
        if isinstance(value, str):
            return cls._safe_text(value)
        if isinstance(value, dict):
            return {cls._safe_text(k): cls._safe_metadata_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._safe_metadata_value(v) for v in value]
        return value

    async def _query_episodic(
        self,
        query_text: str,
        n_results: int,
        where: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._episodic.query,
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )

    @staticmethod
    def _extract_docs(results: Dict[str, Any]) -> List[str]:
        docs = results.get("documents") or []
        if not docs:
            return []
        first = docs[0] if isinstance(docs[0], list) else docs
        return [doc for doc in first if isinstance(doc, str) and doc.strip()]

    @staticmethod
    def _dedupe_texts(values: List[str]) -> List[str]:
        seen = set()
        deduped: List[str] = []
        for value in values:
            text = value.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    async def _merge_summary(self, old_summary: str, new_summary: str) -> str:
        old_summary = self._safe_text(old_summary).strip()
        new_summary = self._safe_text(new_summary).strip()
        if not old_summary:
            return new_summary[: self.SUMMARY_MAX_CHARS]
        if not new_summary:
            return old_summary[: self.SUMMARY_MAX_CHARS]

        prompt = self._safe_text(
            f"""你是对话摘要器。请把下面两段摘要合并为一段不超过 {self.SUMMARY_MAX_CHARS} 个中文字符的摘要。
保留：用户偏好、关键实体、待办事项、约束条件、未解决问题。
只输出摘要正文，不要编号，不要解释。

旧摘要:
{old_summary}

新增摘要:
{new_summary}
"""
        )
        try:
            resp = await self._client.messages.create(
                **llm_request_options(),
                model=self._model,
                max_tokens=256,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            merged = self._safe_text(extract_text_content(resp.content)).strip()
            if merged:
                return merged[: self.SUMMARY_MAX_CHARS]
        except Exception as ex:
            logger.warning(f"合并摘要失败，回退为截断拼接: {ex}")

        merged = self._safe_text(f"{old_summary}\n{new_summary}").strip()
        return merged[-self.SUMMARY_MAX_CHARS :]

    @staticmethod
    def _latest_profile_from_results(results: Dict[str, Any]) -> Dict[str, Any]:
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        if not documents:
            return {}

        candidates: List[tuple[str, Dict[str, Any], str]] = []
        for idx, doc in enumerate(documents):
            if not doc:
                continue
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            ts = str(metadata.get("updated_at") or metadata.get("ts") or "")
            candidates.append((ts, metadata, doc))

        if not candidates:
            return {}

        candidates.sort(key=lambda item: item[0], reverse=True)
        latest_doc = candidates[0][2]
        try:
            return json.loads(latest_doc)
        except Exception:
            return {}
