"""Explicit pinned model preparation. Runtime verification never uses the network.

download: fetch fixed official source files (or verify a complete local cache).
export: run the existing offline exporter with an explicit isolated Python.
verify: validate packaged assets; optionally load and encode one smoke sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.intent_embeddings import EmbeddingConfig, OnnxSemanticBackend, manifest_space_id
from scripts.export_intent_embedding import MODEL_ID, REVISION, SOURCE_HASHES

DEFAULT_SOURCE = ROOT / "data/models/intent" / ("source-" + REVISION)
DEFAULT_MODEL = ROOT / "data/models/intent/bge-small-zh-v1.5"
DEFAULT_MANIFEST = ROOT / "config/intent_embedding_model.json"


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_source(source_dir: Path, *, offline: bool = False) -> dict:
    """Do not overwrite a mismatched cache or expose an incomplete final file."""
    downloaded = 0
    for name, expected in SOURCE_HASHES.items():
        destination = source_dir / name
        if destination.is_file():
            if file_hash(destination) != expected:
                raise ValueError(f"Pinned source checksum mismatch: {name}")
            continue
        if offline:
            raise FileNotFoundError(f"Pinned source is missing: {name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"
                with urllib.request.urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, stream)
            if file_hash(temporary) != expected:
                raise ValueError(f"Downloaded source checksum mismatch: {name}")
            os.replace(temporary, destination)
            downloaded += 1
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"model_id": MODEL_ID, "revision": REVISION, "verified_files": len(SOURCE_HASHES),
            "downloaded_files": downloaded, "offline": offline}


def verify_runtime(model_dir: Path, manifest_path: Path, *, smoke: bool = False) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("model_id") != MODEL_ID or manifest.get("revision") != REVISION:
        raise ValueError("Model identity does not match the pinned preparation source")
    if manifest.get("space_id") != manifest_space_id(manifest):
        raise ValueError("Manifest fingerprint mismatch")
    expected = {name: manifest["files"][name]["sha256"] for name in ("model.onnx", "tokenizer.json")}
    expected["MODEL_CARD.md"] = SOURCE_HASHES["README.md"]
    for name, digest in expected.items():
        if file_hash(model_dir / name) != digest:
            raise ValueError(f"Runtime checksum mismatch: {name}")
    result = {"model_id": MODEL_ID, "revision": REVISION, "space_id": manifest["space_id"],
              "verified_files": expected, "smoke": False}
    if smoke:
        backend = OnnxSemanticBackend(EmbeddingConfig(model_dir=model_dir, manifest_path=manifest_path))
        backend.initialize()
        vectors, truncated = backend.encode_batch(["请问明天眼科还有号吗？"])
        from core.intent_embeddings import validate_vectors
        validate_vectors(vectors, count=1, dimension=512)
        result.update(smoke=True, dimension=len(vectors[0]), truncated=truncated)
    return result


def export_runtime(source_dir: Path, model_dir: Path, manifest_path: Path,
                   python: Path, evidence: Path) -> dict:
    download_source(source_dir, offline=True)
    # Never install packages or download implicitly. The caller prepares the build venv.
    subprocess.run([str(python), str(ROOT / "scripts/export_intent_embedding.py"),
                    "--source-dir", str(source_dir), "--output-dir", str(model_dir),
                    "--manifest", str(manifest_path), "--evidence", str(evidence)],
                   check=True, env=os.environ | {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    return verify_runtime(model_dir, manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Fetch fixed sources; --offline only checks the cache")
    download.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    download.add_argument("--offline", action="store_true")
    export = commands.add_parser("export", help="Export offline using an already prepared isolated Python")
    export.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    export.add_argument("--python", type=Path, required=True)
    export.add_argument("--evidence", type=Path, required=True)
    verify = commands.add_parser("verify", help="Validate local assets; never download or fall back")
    verify.add_argument("--smoke", action="store_true")
    for command in (export, verify):
        command.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
        command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            result = download_source(args.source_dir, offline=args.offline)
        elif args.command == "export":
            result = export_runtime(args.source_dir, args.model_dir, args.manifest, args.python, args.evidence)
        else:
            result = verify_runtime(args.model_dir, args.manifest, smoke=args.smoke)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Model preparation failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
