"""Prepare or run one existing browser acceptance with U002 source/embedding evidence.

No network, services, .env reads, or model calls without --execute. Coordinate
U002-08 freeze before execution; this wrapper never retries or accepts a baseline.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "AGENTS.md").exists())
MODEL = ROOT / "config/intent_embedding_model.json"
CALIBRATION = ROOT / "config/intent_embedding_calibration.json"


def load_runner(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tests/browser/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def baseline_snapshot(container):
    code = ("from pathlib import Path; import hashlib,json; p=Path('/app/data/eval/baseline.json'); "
            "print(json.dumps({'baseline_sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None}))")
    return json.loads(subprocess.check_output(["docker", "exec", container, "python", "-c", code], text=True))


def diagnostics(report, mode):
    samples = {}
    if mode != "evaluation":
        for index, entry in enumerate(report.get("responses", [])):
            if (entry.get("url") or entry.get("path") or "").endswith("/chat"):
                samples[f"chat:{index}"] = entry["data"].get("intent_embedding", {})
    else:
        for result in report.get("results", []):
            metadata = result.get("metadata", {})
            if metadata.get("kind") == "intent":
                for index, case in enumerate(metadata.get("cases", [])):
                    samples[f"intent:{case.get('id') or index}"] = case.get("intent_embedding", {})
            turns = metadata.get("turns", [])
            if "question" in metadata:
                turns = [*turns, metadata]
            for turn in turns:
                # A quality row and its business row contain the same turn; count once.
                key = f"turn:{turn.get('conv_id')}:{turn.get('turn')}"
                samples[key] = turn.get("intent_embedding", {})
    counts = Counter(value.get("status", "missing") for value in samples.values())
    fallbacks = {key: value for key, value in samples.items()
                 if value.get("status") not in {"semantic_ready", "bypassed_emergency"}}
    return {"sample_counts_by_status": dict(counts), "samples": samples,
            "non_semantic_samples": fallbacks,
            "note": "Unique classified messages and business turns; not evaluator result-row accuracy."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "health", "evaluation"), required=True)
    parser.add_argument("--url", default="http://127.0.0.1:28088")
    parser.add_argument("--expected-api", default="http://127.0.0.1:28000")
    parser.add_argument("--container", default="medipet-u002-validation-api-1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    prepared = {"phase": "prepared_only", "mode": args.mode, "model_space_id": model["space_id"],
                "calibration_exists": CALIBRATION.exists(), "real_requests": 0,
                "ready_for_execution": False, "requires": "Coordinator confirms U002-08 freeze and rebuilt isolated services"}
    if not args.execute:
        print(json.dumps(prepared, ensure_ascii=False, indent=2))
        return 0
    if not CALIBRATION.is_file():
        raise RuntimeError("U002-08 calibration is not present; do not execute")
    args.output.mkdir(parents=True, exist_ok=False)
    evaluator = load_runner("run_evaluation")
    original_manifest = evaluator.source_manifest

    def manifest():
        return original_manifest() | {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
                                     for path in (MODEL, CALIBRATION, Path(__file__))}

    evaluator.source_manifest = manifest
    before = manifest()
    save(args.output / "u002-source-before.json", before)
    baseline_before = baseline_snapshot(args.container)
    save(args.output / "container-before.json", baseline_before)
    for url in (args.expected_api + "/health", args.url + "/api/python/health"):
        with urllib.request.urlopen(url, timeout=10) as response:
            status = json.load(response)["intent_embedding"]
        if status.get("status") != "semantic_ready" or status.get("space_id") != model["space_id"]:
            raise RuntimeError(f"Service is not using the frozen semantic model: {status}")
    destination = args.output / "result"
    result_file = destination / {"evaluation": "response.json", "demo": "business.json", "health": "health.json"}[args.mode]
    try:
        if args.mode == "evaluation":
            evaluator.run_once(args.url, destination, 1800, args.expected_api)
        elif args.mode == "demo":
            load_runner("check_demo").check_demo(args.url, destination)
        else:
            load_runner("check_health").check_health(args.url, destination)
    finally:
        if result_file.exists():
            report = json.loads(result_file.read_text(encoding="utf-8"))
            save(args.output / "embedding-diagnostics.json", diagnostics(report, args.mode))
        after = manifest()
        save(args.output / "u002-source-after.json", after)
        baseline_after = baseline_snapshot(args.container)
        save(args.output / "container-after.json", baseline_after)
        save(args.output / "source-stability.json", {"unchanged": before == after,
             "changed": [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)],
             "accepted_baseline_unchanged": baseline_before == baseline_after,
             "baseline_policy": "candidate only; no acceptance request; failures are not retried"})
    if before != after or baseline_before != baseline_after:
        raise RuntimeError("Frozen source or accepted baseline changed; evidence retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
