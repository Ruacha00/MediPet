"""Reproduce U002-05 without a server, keys, network, or existing data volumes."""
import asyncio
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import tempfile
import time

from core.intent_embeddings import EmbeddingConfig, IntentEmbeddingProvider
from scripts.prepare_intent_embedding import verify_runtime


def forbidden_connect(*args, **kwargs):
    raise AssertionError("offline probe attempted a network connection")


async def main():
    socket.socket.connect = forbidden_connect
    config = EmbeddingConfig.from_env()
    verify_runtime(config.model_dir, config.manifest_path)
    assert os.getuid() != 0
    assert not os.access(config.model_dir / "model.onnx", os.W_OK)
    assert importlib.util.find_spec("torch") is None
    assert importlib.util.find_spec("transformers") is None
    provider = IntentEmbeddingProvider(config)
    try:
        started = time.perf_counter()
        status = await provider.initialize()
        cold_ms = (time.perf_counter() - started) * 1000
        assert status["status"] == "semantic_ready", status
        samples = ["请问明天眼科还有号吗？", "检查结果上的参考范围是什么意思？", "门诊" * 400]
        values = []
        flags = []
        for index in range(30):
            batch = await provider.encode_batch([samples[index % len(samples)]])
            assert batch.backend == "semantic" and batch.dimension == 512
            values.append(batch.elapsed_ms)
            flags.append(batch.truncated[0])
        assert any(flags) and not all(flags)
    finally:
        await provider.aclose()
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    chroma_vectors = ONNXMiniLM_L6_V2()(["MediPet offline compatibility check"])
    assert len(chroma_vectors[0]) == 384
    failures = {}
    for kind in ("missing", "corrupt"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            if kind == "corrupt":
                (path / "model.onnx").write_bytes(b"corrupt probe asset")
            probe = IntentEmbeddingProvider(EmbeddingConfig(model_dir=path))
            try:
                failures[kind] = await probe.initialize()
                assert failures[kind]["status"] == "hash_fallback"
                batch = await probe.encode_batch(["查询预约"])
                assert batch.backend == "hash" and batch.dimension == 256
            finally:
                await probe.aclose()
    print(json.dumps({
        "python": platform.python_version(), "platform": platform.platform(), "uid": os.getuid(),
        "model_directory": str(config.model_dir), "model_readonly": True, "network_connect_forbidden": True,
        "versions": {name: importlib.metadata.version(name) for name in ("onnxruntime", "tokenizers", "numpy", "chromadb")},
        "production_has_torch": False, "production_has_transformers": False, "status": status,
        "cold_initialize_ms": cold_ms, "sample_count": 30, "p50_ms": statistics.median(values),
        "p95_ms": sorted(values)[28], "max_ms": max(values), "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "chroma_embedding_dimension": 384, "failure_states": failures, "passed": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
