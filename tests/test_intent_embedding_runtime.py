"""Preparation failure contracts; real ONNX smoke requires an explicit opt-in."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from core.intent_embeddings import EmbeddingConfig, IntentEmbeddingProvider, manifest_space_id
from scripts import prepare_intent_embedding as prepare


def digest(value):
    return hashlib.sha256(value).hexdigest()


def test_download_is_pinned_atomic_and_reuses_verified_cache(tmp_path, monkeypatch):
    content = b"fixed source"
    monkeypatch.setattr(prepare, "SOURCE_HASHES", {"nested/config.json": digest(content)})
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        assert not (tmp_path / "nested/config.json").exists()
        return io.BytesIO(content)

    monkeypatch.setattr(prepare.urllib.request, "urlopen", fetch)
    assert prepare.download_source(tmp_path)["downloaded_files"] == 1
    assert prepare.download_source(tmp_path)["downloaded_files"] == 0
    assert calls == [(f"https://huggingface.co/{prepare.MODEL_ID}/resolve/{prepare.REVISION}/nested/config.json", 60)]
    assert list(tmp_path.rglob("*.part")) == []


def test_bad_download_never_publishes_partial_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "SOURCE_HASHES", {"config.json": digest(b"expected")})
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"wrong"))
    with pytest.raises(ValueError, match="Downloaded source checksum"):
        prepare.download_source(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_offline_missing_cache_never_attempts_network(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    assert prepare.main(["download", "--offline", "--source-dir", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []


def test_existing_corrupt_cache_is_preserved_and_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "SOURCE_HASHES", {"config.json": digest(b"expected")})
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    (tmp_path / "config.json").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="Pinned source checksum"):
        prepare.download_source(tmp_path)
    assert (tmp_path / "config.json").read_bytes() == b"corrupted"


@pytest.fixture
def package(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    contents = {"model.onnx": b"onnx", "tokenizer.json": b"tokenizer", "MODEL_CARD.md": b"license"}
    for name, content in contents.items():
        (model / name).write_bytes(content)
    monkeypatch.setattr(prepare, "SOURCE_HASHES", {"README.md": digest(contents["MODEL_CARD.md"])})
    data = {"model_id": prepare.MODEL_ID, "revision": prepare.REVISION,
            "files": {name: {"sha256": digest(content)} for name, content in contents.items()}}
    data["space_id"] = manifest_space_id(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    return model, manifest, data


def test_verify_is_offline_and_does_not_load_model_unless_requested(package, monkeypatch):
    model, manifest, _ = package
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    monkeypatch.setattr(prepare.OnnxSemanticBackend, "initialize", lambda *a: pytest.fail("model loaded"))
    assert prepare.verify_runtime(model, manifest)["smoke"] is False


@pytest.mark.parametrize("filename", ["model.onnx", "tokenizer.json", "MODEL_CARD.md"])
def test_each_corrupted_packaged_asset_causes_preparation_failure(package, filename):
    model, manifest, _ = package
    (model / filename).write_bytes(b"damaged")
    assert prepare.main(["verify", "--model-dir", str(model), "--manifest", str(manifest)]) == 1


def test_manifest_drift_is_rejected(package):
    model, manifest, data = package
    data["pooling"] = "mean"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fingerprint"):
        prepare.verify_runtime(model, manifest)


def test_failed_export_is_not_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "download_source", lambda *a, **k: {})

    def fail(command, **kwargs):
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["env"]["TRANSFORMERS_OFFLINE"] == "1"
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(prepare.subprocess, "run", fail)
    assert prepare.main(["export", "--python", "missing-python", "--source-dir", str(tmp_path),
                         "--model-dir", str(tmp_path / "model"), "--evidence", str(tmp_path / "evidence.json")]) == 1


@pytest.mark.parametrize("broken", [False, True])
def test_missing_or_corrupt_model_reports_fallback_without_download(tmp_path, monkeypatch, broken):
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    if broken:
        (tmp_path / "model.onnx").write_bytes(b"damaged")
    async def check():
        provider = IntentEmbeddingProvider(EmbeddingConfig(model_dir=tmp_path))
        try:
            status = await provider.initialize()
            assert status["status"] == "hash_fallback"
            expected = "ValueError" if broken else "FileNotFoundError"
            assert status["fallback_reason"] == f"initialize:{expected}"
            assert (await provider.encode_batch(["挂号"])).dimension == 256
        finally:
            await provider.aclose()
    asyncio.run(check())


@pytest.mark.skipif(os.getenv("MEDIPET_TEST_REAL_INTENT_EMBEDDING") != "1", reason="explicit local ONNX opt-in")
def test_real_prepared_model_loads_with_network_forbidden(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network called"))
    result = prepare.verify_runtime(prepare.DEFAULT_MODEL, prepare.DEFAULT_MANIFEST, smoke=True)
    assert result["dimension"] == 512 and result["truncated"] == [False]
