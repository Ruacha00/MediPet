"""Local intent encoders. No chat client, calibration, templates, or network IO."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
HASH_SPACE_ID = "hash:md5-char-ngram-1-2-3-set-signed-v1:256"


class EmbeddingUnavailable(RuntimeError):
    """The entire vector branch is unavailable; callers must abstain."""


@dataclass(frozen=True)
class EmbeddingConfig:
    backend: str = "semantic"
    model_dir: Path = ROOT / "data/models/intent/bge-small-zh-v1.5"
    fallback: str = "hash"
    timeout_ms: int = 2000
    threads: int = 2
    manifest_path: Path = ROOT / "config/intent_embedding_model.json"

    def __post_init__(self):
        if self.backend not in {"semantic", "hash", "disabled"}:
            raise ValueError("invalid intent embedding backend")
        if self.fallback not in {"hash", "disabled"}:
            raise ValueError("invalid intent embedding fallback")
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int) or self.timeout_ms <= 0:
            raise ValueError("embedding timeout_ms must be a positive integer")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads <= 0:
            raise ValueError("embedding threads must be a positive integer")
        object.__setattr__(self, "model_dir", Path(self.model_dir))
        object.__setattr__(self, "manifest_path", Path(self.manifest_path))

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        prefix = "MEDIPET_INTENT_EMBEDDING_"
        return cls(
            backend=os.getenv(prefix + "BACKEND", "semantic"),
            model_dir=Path(os.getenv(prefix + "MODEL_DIR", str(cls.model_dir))),
            fallback=os.getenv(prefix + "FALLBACK", "hash"),
            timeout_ms=int(os.getenv(prefix + "TIMEOUT_MS", "2000")),
            threads=int(os.getenv(prefix + "THREADS", "2")),
        )


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    space_id: str
    generation: int
    backend: str
    model: str
    dimension: int
    elapsed_ms: float
    truncated: list[bool]


def hash_embedding(text: str, dims: int = 256) -> list[float]:
    """Preserve the original signed MD5 character n-gram set algorithm exactly."""
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


def validate_vectors(vectors: Any, *, count: int, dimension: int) -> list[list[float]]:
    if len(vectors) != count:
        raise ValueError("embedding batch count mismatch")
    result = []
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("embedding dimension mismatch")
        values = [float(x) for x in vector]
        if not all(math.isfinite(x) for x in values):
            raise ValueError("embedding contains non-finite values")
        if not any(x != 0 for x in values):
            raise ValueError("embedding has zero norm")
        result.append(values)
    return result


def manifest_space_id(manifest: dict) -> str:
    """The complete immutable asset/processing manifest defines the semantic space."""
    payload = {key: value for key, value in manifest.items() if key != "space_id"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "semantic:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OnnxSemanticBackend:
    """Synchronous, offline backend, always invoked through the provider worker."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.model = "BAAI/bge-small-zh-v1.5"
        self.dimension = 512
        self.space_id = "semantic:uninitialized"
        self._session = None
        self._tokenizer = None

    def initialize(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        manifest = json.loads(self.config.manifest_path.read_text(encoding="utf-8"))
        if (manifest["model_id"] != self.model or manifest["dimension"] != 512
                or manifest["pooling"] != "cls" or manifest["normalization"] != "l2"
                or manifest["max_tokens"] != 512 or manifest["prefix"] != ""):
            raise ValueError("unsupported intent model processing manifest")
        expected_space = manifest_space_id(manifest)
        if manifest.get("space_id") != expected_space:
            raise ValueError("intent model manifest fingerprint mismatch")
        for filename in ("model.onnx", "tokenizer.json"):
            path = self.config.model_dir / filename
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != manifest["files"][filename]["sha256"]:
                raise ValueError("intent model asset checksum mismatch")
        tokenizer = Tokenizer.from_file(str(self.config.model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=512, strategy="longest_first", direction="right")
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", direction="right")
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.config.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(self.config.model_dir / "model.onnx"),
                                       sess_options=options, providers=["CPUExecutionProvider"])
        self._tokenizer, self._session = tokenizer, session
        self.space_id = expected_space

    def encode_batch(self, texts: Sequence[str]) -> tuple[list[list[float]], list[bool]]:
        import numpy as np

        vectors, truncated = [], []
        # A long example must not force every short example to allocate 512 tokens.
        for start in range(0, len(texts), 16):
            encodings = self._tokenizer.encode_batch(list(texts[start:start + 16]))
            inputs = {
                "input_ids": np.asarray([item.ids for item in encodings], dtype=np.int64),
                "attention_mask": np.asarray([item.attention_mask for item in encodings], dtype=np.int64),
                "token_type_ids": np.asarray([item.type_ids for item in encodings], dtype=np.int64),
            }
            inputs = {item.name: inputs[item.name] for item in self._session.get_inputs()}
            output = self._session.run(["sentence_embedding"], inputs)[0]
            vectors.extend(output.tolist())
            truncated.extend(bool(item.overflowing) for item in encodings)
        return vectors, truncated


class IntentEmbeddingProvider:
    """One shared encoder; all caches and calibration belong to each recognizer.

    A timed-out worker keeps its slot until it really finishes. Its result is never
    published. Fallback latches for this provider's lifetime; create a new provider
    to explicitly recover. The owner alone calls aclose().
    """

    def __init__(self, config: EmbeddingConfig | None = None, *, semantic_backend=None):
        self.config = config or EmbeddingConfig.from_env()
        self._semantic = semantic_backend or OnnxSemanticBackend(self.config)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intent-embedding")
        self._gate = asyncio.Lock()
        self._init_gate = asyncio.Lock()
        self._inflight = None
        self._closed = False
        self._initialized = self.config.backend != "semantic"
        self._generation = 0
        self._active = self.config.backend
        self._state = {"hash": "hash_explicit", "disabled": "disabled_explicit",
                       "semantic": "uninitialized"}[self._active]
        self._fallback_reason = None
        self._failures = 0

    def status(self) -> dict[str, Any]:
        semantic = self._active == "semantic"
        return {
            "configured_backend": self.config.backend,
            "active_backend": self._active,
            "model": self._semantic.model if semantic else ("character-ngram-md5" if self._active == "hash" else None),
            "space_id": self._semantic.space_id if semantic else (HASH_SPACE_ID if self._active == "hash" else "disabled"),
            "dimension": self._semantic.dimension if semantic else (256 if self._active == "hash" else 0),
            "status": "closed" if self._closed else self._state,
            "generation": self._generation,
            "fallback_reason": self._fallback_reason,
            "failure_count": self._failures,
            "inference_in_flight": bool(self._inflight is not None and not self._inflight.done()),
        }

    def _fallback(self, stage: str, error: Exception) -> None:
        if self._active != "semantic":
            return
        self._active = self.config.fallback
        self._state = self._active + "_fallback"
        # No exception text: it can contain paths or user text from a third party.
        self._fallback_reason = f"{stage}:{type(error).__name__}"
        self._generation += 1
        self._failures += 1
        self._initialized = True

    async def _run_sync(self, function, deadline: float):
        if self._closed:
            raise EmbeddingUnavailable("embedding provider closed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("embedding request deadline exceeded")
        await asyncio.wait_for(self._gate.acquire(), remaining)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._closed:
                raise TimeoutError("embedding request deadline exceeded")
            future = asyncio.get_running_loop().run_in_executor(self._executor, function)
            self._inflight = future
        except BaseException:
            self._gate.release()
            raise
        def release(done):
            # Retrieve an exception even after timeout/cancellation to avoid leaks.
            if not done.cancelled():
                done.exception()
            self._gate.release()
        future.add_done_callback(release)
        return await asyncio.wait_for(asyncio.shield(future), remaining)

    async def _initialize(self, deadline: float):
        if self._closed:
            raise EmbeddingUnavailable("embedding provider closed")
        if self._initialized:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("embedding initialization deadline exceeded")
        await asyncio.wait_for(self._init_gate.acquire(), remaining)
        try:
            if self._initialized:
                return
            try:
                await self._run_sync(self._semantic.initialize, deadline)
                self._state = "semantic_ready"
                self._generation += 1
                self._initialized = True
            except Exception as error:
                self._fallback("initialize", error)
        finally:
            self._init_gate.release()

    async def initialize(self) -> dict[str, Any]:
        await self._initialize(time.monotonic() + self.config.timeout_ms / 1000)
        return self.status()

    async def encode_batch(self, texts: Sequence[str]) -> EmbeddingBatch:
        if isinstance(texts, (str, bytes)) or any(not isinstance(text, str) for text in texts):
            raise ValueError("embedding input must be a sequence of strings")
        cleaned = [text.encode("utf-8", errors="ignore").decode("utf-8") for text in texts]
        if any(not text.strip() for text in cleaned):
            raise ValueError("embedding input contains an empty text")
        started = time.monotonic()
        deadline = started + self.config.timeout_ms / 1000
        await self._initialize(deadline)
        for attempt in range(2):
            state = self.status()
            if state["active_backend"] == "disabled" or self._closed:
                raise EmbeddingUnavailable("embedding branch disabled")
            semantic = state["active_backend"] == "semantic"
            def encode(state=state, semantic=semantic):
                if state["generation"] != self._generation:
                    raise EmbeddingUnavailable("embedding generation changed before encoding")
                if semantic:
                    vectors, truncated = self._semantic.encode_batch(cleaned)
                else:
                    vectors, truncated = [hash_embedding(text) for text in cleaned], [False] * len(cleaned)
                values = validate_vectors(vectors, count=len(cleaned), dimension=state["dimension"])
                if len(truncated) != len(cleaned) or any(type(value) is not bool for value in truncated):
                    raise ValueError("embedding truncation flags mismatch")
                return values, truncated
            try:
                vectors, truncated = await self._run_sync(encode, deadline)
                if state["generation"] != self._generation or state["space_id"] != self.status()["space_id"]:
                    raise EmbeddingUnavailable("embedding generation changed during encoding")
                return EmbeddingBatch(vectors, state["space_id"], state["generation"], state["active_backend"],
                                      state["model"], state["dimension"], (time.monotonic() - started) * 1000, truncated)
            except Exception as error:
                if semantic:
                    self._fallback("encode", error)
                else:
                    raise EmbeddingUnavailable("hash embedding unavailable") from error
        raise EmbeddingUnavailable("embedding branch unavailable")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        if self._inflight is not None:
            await asyncio.gather(asyncio.shield(self._inflight), return_exceptions=True)
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        if isinstance(self._semantic, OnnxSemanticBackend):
            self._semantic._session = None
            self._semantic._tokenizer = None
