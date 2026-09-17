"""Offline contracts; real local ONNX tests require an explicit opt-in."""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import threading

import pytest

from core.intent_embeddings import (
    EmbeddingConfig, EmbeddingUnavailable, HASH_SPACE_ID, IntentEmbeddingProvider,
    OnnxSemanticBackend, hash_embedding, manifest_space_id, validate_vectors,
)


class FakeBackend:
    model = "test-encoder"
    dimension = 3
    space_id = "test:fixed-v1"

    def __init__(self):
        self.initializations = 0
        self.calls = []

    def initialize(self):
        self.initializations += 1

    def encode_batch(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 2.0, 3.0] for _ in texts], [False] * len(texts)


@pytest.mark.parametrize("text,expected", [
    ("挂号", "283ab663fb9fe92ee7d9c7225effd112d6b06699fef9229bae4351414a479f62"),
    ("Hello 世界", "ee3da5e92a253971fbe2fee57c24eeeaeb86f940f78ea22616152f05b0cc3fdb"),
    ("预约 Appointment", "58423bd14ce03feab62ca087a71b114c426d5a017d8a5fdee1e4f99c8831761e"),
    ("", "be2020e4cbc7ea517a05caf54d889d6861139971b43f64c8a9056e4f3baa1d51"),
    (" ABC ", "9bcb76550304834cf74174e8c7067df6d45439f0bb9e3f23a5999926d8fdfd3b"),
])
def test_hash_matches_frozen_legacy_output(text, expected):
    raw = json.dumps(hash_embedding(text), separators=(",", ":")).encode()
    assert hashlib.sha256(raw).hexdigest() == expected


@pytest.mark.parametrize("kwargs", [{"backend": "remote"}, {"fallback": "other"},
                                      {"threads": 0}, {"timeout_ms": 0}, {"threads": True}])
def test_invalid_config_is_not_silently_fallback(kwargs):
    with pytest.raises(ValueError):
        EmbeddingConfig(**kwargs)


def test_environment_config_is_independent_from_chat(monkeypatch):
    monkeypatch.setenv("MEDIPET_INTENT_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("MEDIPET_INTENT_EMBEDDING_THREADS", "3")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://invalid.example")
    cfg = EmbeddingConfig.from_env()
    assert cfg.backend == "hash" and cfg.threads == 3


@pytest.mark.parametrize("vectors,count,dimension", [
    ([[1.0, 2]], 2, 2), ([[1.0, 2]], 1, 3), ([[float("nan"), 1]], 1, 2),
    ([[float("inf"), 1]], 1, 2), ([[0, 0]], 1, 2),
])
def test_invalid_vector_batch_is_rejected(vectors, count, dimension):
    with pytest.raises(ValueError):
        validate_vectors(vectors, count=count, dimension=dimension)


def test_manifest_space_changes_for_processing_assets_and_revision():
    base = {"revision": "a", "pooling": "cls", "files": {"model.onnx": {"sha256": "x"}}}
    original = manifest_space_id(base)
    assert manifest_space_id(base | {"space_id": original}) == original
    assert manifest_space_id(base | {"pooling": "mean"}) != original
    assert manifest_space_id(base | {"revision": "b"}) != original
    assert manifest_space_id(base | {"files": {"model.onnx": {"sha256": "y"}}}) != original


def test_shared_initialize_once_and_batch_space_stays_uniform():
    async def run():
        fake = FakeBackend()
        provider = IntentEmbeddingProvider(semantic_backend=fake)
        try:
            results = await asyncio.gather(*(provider.encode_batch([f"消息{i}", "挂号"]) for i in range(8)))
            assert fake.initializations == 1
            assert len(fake.calls) == 8
            assert {item.space_id for item in results} == {fake.space_id}
            assert {item.generation for item in results} == {1}
            assert all(item.dimension == 3 and len(item.vectors) == 2 for item in results)
            assert provider.status()["status"] == "semantic_ready"
        finally:
            await provider.aclose()
        assert provider.status()["status"] == "closed"
        with pytest.raises(EmbeddingUnavailable):
            await provider.encode_batch(["你好"])
    asyncio.run(run())


@pytest.mark.parametrize("texts", ["not-a-list", [123], [""], [" \t"], ["\ud800"]])
def test_bad_text_does_not_change_backend(texts):
    async def run():
        fake = FakeBackend()
        provider = IntentEmbeddingProvider(semantic_backend=fake)
        try:
            with pytest.raises(ValueError):
                await provider.encode_batch(texts)
            assert provider.status()["generation"] == 0
            assert fake.initializations == 0
        finally:
            await provider.aclose()
    asyncio.run(run())


def test_empty_batch_and_invalid_unicode_are_deterministic():
    async def run():
        fake = FakeBackend()
        provider = IntentEmbeddingProvider(semantic_backend=fake)
        try:
            assert (await provider.encode_batch([])).vectors == []
            await provider.encode_batch(["你好\ud800"])
            assert fake.calls[-1] == ["你好"]
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("fallback,expected", [("hash", "hash_fallback"), ("disabled", "disabled_fallback")])
def test_missing_model_latches_configured_fallback_without_retry(tmp_path, fallback, expected):
    async def run():
        provider = IntentEmbeddingProvider(EmbeddingConfig(model_dir=tmp_path, fallback=fallback))
        try:
            first = await provider.initialize()
            assert first["status"] == expected and first["generation"] == 1
            second = await provider.initialize()
            assert second["failure_count"] == 1
            assert str(tmp_path) not in json.dumps(second)
            if fallback == "hash":
                batch = await provider.encode_batch(["挂号"])
                assert batch.space_id == HASH_SPACE_ID and batch.vectors == [hash_embedding("挂号")]
            else:
                with pytest.raises(EmbeddingUnavailable):
                    await provider.encode_batch(["挂号"])
        finally:
            await provider.aclose()
    asyncio.run(run())


def test_explicit_disabled_and_hash_never_initialize_semantic_backend():
    async def run():
        for mode in ("disabled", "hash"):
            fake = FakeBackend()
            provider = IntentEmbeddingProvider(EmbeddingConfig(backend=mode), semantic_backend=fake)
            try:
                assert (await provider.initialize())["status"] == mode + "_explicit"
                if mode == "hash":
                    assert (await provider.encode_batch(["挂号"])).dimension == 256
                else:
                    with pytest.raises(EmbeddingUnavailable):
                        await provider.encode_batch(["挂号"])
                assert fake.initializations == 0 and fake.calls == []
            finally:
                await provider.aclose()
    asyncio.run(run())


def test_corrupt_local_asset_is_rejected_before_inference(tmp_path):
    manifest_path = Path(__file__).resolve().parents[1] / "config/intent_embedding_model.json"
    (tmp_path / "model.onnx").write_bytes(b"corrupt model")
    async def run():
        provider = IntentEmbeddingProvider(EmbeddingConfig(model_dir=tmp_path, manifest_path=manifest_path))
        try:
            state = await provider.initialize()
            assert state["status"] == "hash_fallback"
            assert state["fallback_reason"] == "initialize:ValueError"
            assert (await provider.encode_batch(["挂号"])).backend == "hash"
        finally:
            await provider.aclose()
    asyncio.run(run())


def test_invalid_semantic_row_falls_back_whole_batch_once():
    class Invalid(FakeBackend):
        def encode_batch(self, texts):
            self.calls.append(texts)
            return [[1, 2, 3], [float("nan"), 2, 3]], [False, False]
    async def run():
        fake = Invalid()
        provider = IntentEmbeddingProvider(semantic_backend=fake)
        try:
            batch = await provider.encode_batch(["挂号", "眼科"])
            assert batch.backend == "hash" and batch.generation == 2
            assert batch.vectors == [hash_embedding("挂号"), hash_embedding("眼科")]
            await provider.encode_batch(["你好"])
            assert len(fake.calls) == 1 and provider.status()["failure_count"] == 1
        finally:
            await provider.aclose()
    asyncio.run(run())


def test_worker_does_not_block_event_loop_and_timeout_does_not_queue_more_work():
    entered, release = threading.Event(), threading.Event()
    class Slow(FakeBackend):
        def encode_batch(self, texts):
            self.calls.append(list(texts))
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test coordination timeout")
            return [[1, 2, 3] for _ in texts], [False] * len(texts)
    async def run():
        fake = Slow()
        provider = IntentEmbeddingProvider(EmbeddingConfig(timeout_ms=70), semantic_backend=fake)
        try:
            task = asyncio.create_task(provider.encode_batch(["挂号"]))
            assert await asyncio.to_thread(entered.wait, 1)
            # Reached on the event loop while the encoder is still blocked.
            assert not release.is_set() and not task.done()
            with pytest.raises(EmbeddingUnavailable):
                await task
            assert provider.status()["status"] == "hash_fallback"
            assert provider.status()["inference_in_flight"]
            with pytest.raises(EmbeddingUnavailable):
                await provider.encode_batch(["眼科"])
            assert fake.calls == [["挂号"]]
            release.set()
            await asyncio.shield(provider._inflight)
            batch = await provider.encode_batch(["眼科"])
            assert batch.backend == "hash" and batch.generation == 2
        finally:
            release.set()
            await provider.aclose()
    asyncio.run(run())


def test_cancelled_request_does_not_release_running_worker_slot():
    entered, release = threading.Event(), threading.Event()
    class Waiting(FakeBackend):
        def encode_batch(self, texts):
            entered.set()
            release.wait(3)
            return super().encode_batch(texts)
    async def run():
        provider = IntentEmbeddingProvider(semantic_backend=Waiting())
        try:
            task = asyncio.create_task(provider.encode_batch(["挂号"]))
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert provider._gate.locked() and provider.status()["inference_in_flight"]
            release.set()
            await asyncio.shield(provider._inflight)
            assert (await provider.encode_batch(["眼科"])).backend == "semantic"
        finally:
            release.set()
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.skipif(os.getenv("MEDIPET_TEST_INTENT_MODEL") != "1", reason="requires prepared local model; never downloads")
def test_real_local_onnx_batch_single_truncation_and_norm():
    async def run():
        provider = IntentEmbeddingProvider(EmbeddingConfig(timeout_ms=15000))
        try:
            batch = await provider.encode_batch(["我要挂号", "appointment 预约", "门诊" * 400])
            assert batch.backend == "semantic" and batch.dimension == 512
            assert batch.truncated == [False, False, True]
            assert all(math.isclose(sum(x * x for x in vec), 1.0, abs_tol=1e-5) for vec in batch.vectors)
            for text, expected in zip(["我要挂号", "appointment 预约", "门诊" * 400], batch.vectors):
                actual = (await provider.encode_batch([text])).vectors[0]
                assert max(abs(x - y) for x, y in zip(expected, actual)) < 1e-5
        finally:
            await provider.aclose()
    asyncio.run(run())
