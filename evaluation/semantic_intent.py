"""U002 offline ranking, one-time LLM capture, and model-free calibrated replay.

No command loads a default dataset. Holdout requires explicit permission and an
approved manifest. Reports remain candidates; this module never accepts a baseline.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

from anthropic import AsyncAnthropic

from core.emergency import detect_emergency
from core.intent_embeddings import EmbeddingConfig, IntentEmbeddingProvider
from core.intent_recognizer import IntentCategory, IntentRecognizer
from core.llm_utils import extract_text_content, llm_request_options

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
FIXED_CLOCK = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def serializable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def fingerprint(value):
    raw = json.dumps(serializable(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    """Atomic publication; callers choose a new path so prior reports survive."""
    path = Path(path)
    if path.exists():
        raise ValueError("output already exists; choose a new candidate path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(serializable(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_dataset(path, *, manifest_path=None, allow_holdout=False):
    path = Path(path)
    document = read_json(path)
    split = document.get("split", "regression")
    if split == "holdout" or path.stem == "holdout":
        if not allow_holdout or not manifest_path:
            raise ValueError("holdout requires --allow-holdout and an approved --manifest")
    manifest_hash = None
    if manifest_path:
        manifest = read_json(manifest_path)
        if manifest.get("review", {}).get("status") != "approved":
            raise ValueError("dataset manifest has not passed independent review")
        expected = manifest.get("file_sha256", {}).get(path.name)
        if expected is None:
            expected = manifest.get("original_regression_sha256", {}).get(
                path.resolve().relative_to(ROOT).as_posix())
        if expected != file_hash(path):
            raise ValueError("dataset does not match frozen manifest")
        manifest_hash = file_hash(manifest_path)
    cases = deepcopy(document["cases"])
    if not cases or len({item["id"] for item in cases}) != len(cases):
        raise ValueError("dataset must contain unique nonempty case IDs")
    for case in cases:
        IntentCategory(case["expected_intent"])
        if not isinstance(case["message"], str) or not case["message"].strip():
            raise ValueError("empty dataset message")
        case.setdefault("history", [])
        case.setdefault("evaluation_group", "regression")
        case.setdefault("vector_primary", not case["history"] and case["expected_intent"] not in {"other", "emergency"})
        case.setdefault("llm_failure_eligible", case["vector_primary"])
        case.setdefault("lexical_overlap", {})
    return {"schema_version": SCHEMA_VERSION, "split": split, "cases": cases,
            "identity": {"dataset_sha256": file_hash(path), "manifest_sha256": manifest_hash,
                         "dataset_id": document.get("dataset_id", path.stem), "split": split}}


def input_for(case):
    # Keep labels, groups, authoring explanations and family names out of requests.
    return {"message": case["message"], "history": case.get("history", [])}


def environment():
    return {"python": platform.python_version(), "platform": platform.platform(),
            "cpu_count": os.cpu_count(), "concurrency": 1}


def source_fingerprints():
    return {name: file_hash(ROOT / name) for name in (
        "core/intent_recognizer.py", "core/intent_embeddings.py", "core/emergency.py", "core/llm_utils.py")}


def identity(dataset, recognizer):
    return {**dataset["identity"], "template_fingerprint": recognizer.template_fingerprint,
            "pattern_fingerprint": recognizer.pattern_fingerprint,
            "source_sha256": source_fingerprints()}


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def timing_summary(values):
    return {"count": len(values), "p50_ms": percentile(values, .50), "p95_ms": percentile(values, .95)}


def peak_rss_bytes():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        measure = ctypes.windll.psapi.GetProcessMemoryInfo
        measure.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not measure(current(), ctypes.byref(counters), counters.cb):
            return None
        return counters.PeakWorkingSetSize
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def classification_metrics(gold, predicted, rankings=None):
    if len(gold) != len(predicted):
        raise ValueError("metric lengths differ")
    labels = sorted(set(gold) | set(predicted))  # Include erroneous OTHER predictions.
    confusion = {label: dict(Counter(p for g, p in zip(gold, predicted) if g == label)) for label in labels}
    per_class = {}
    for label in labels:
        tp = sum(g == p == label for g, p in zip(gold, predicted))
        fp = sum(g != label and p == label for g, p in zip(gold, predicted))
        fn = sum(g == label and p != label for g, p in zip(gold, predicted))
        per_class[label] = {"support": gold.count(label), "predicted": predicted.count(label),
                            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    correct = sum(g == p for g, p in zip(gold, predicted))
    result = {"count": len(gold), "correct": correct, "accuracy": correct / len(gold) if gold else None,
              "macro_f1": statistics.mean(v["f1"] for v in per_class.values()) if labels else None,
              "other_count": predicted.count("other"), "other_rate": predicted.count("other") / len(gold) if gold else None,
              "per_class": per_class, "confusion": confusion}
    if rankings is not None:
        if len(rankings) != len(gold):
            raise ValueError("ranking metric lengths differ")
        top3 = sum(g in ranked[:3] for g, ranked in zip(gold, rankings))
        result.update(top3_correct=top3, top3_accuracy=top3 / len(gold) if gold else None)
    return result


def grouped_metrics(rows, prediction_key, *, ranking=False):
    subsets = {"all": rows, "primary": [row for row in rows if row["vector_primary"]],
               "low_literal_overlap": [row for row in rows if row["low_literal_overlap"]]}
    subsets.update({group: [row for row in rows if row["evaluation_group"] == group]
                    for group in sorted({row["evaluation_group"] for row in rows})})
    result = {}
    for name, subset in subsets.items():
        skipped = 0
        if ranking:
            skipped = sum(row.get("fixed_emergency_bypass", False) for row in subset)
            subset = [row for row in subset if not row.get("fixed_emergency_bypass")]
        result[name] = classification_metrics(
            [row["expected_intent"] for row in subset], [row[prediction_key] for row in subset],
            [[item["intent"] for item in row.get("ranking", [])] for row in subset] if ranking else None)
        if ranking:
            result[name]["fixed_rule_excluded"] = skipped
    return result


def selective_metrics(rows):
    eligible = [row for row in rows if row["llm_failure_eligible"]]
    accepted = [row for row in eligible if row["failure_prediction"] != "other"]
    correct = sum(row["expected_intent"] == row["failure_prediction"] for row in accepted)
    return {"eligible": len(eligible), "accepted": len(accepted), "correct": correct,
            "accuracy": correct / len(accepted) if accepted else None,
            "coverage": len(accepted) / len(eligible) if eligible else None,
            "rejected_ids": [row["id"] for row in eligible if row["failure_prediction"] == "other"],
            "wrongly_accepted_ids": [row["id"] for row in accepted if row["failure_prediction"] != row["expected_intent"]]}


def case_fields(case):
    return {"id": case["id"], "input_sha256": fingerprint(input_for(case)),
            "expected_intent": case["expected_intent"], "evaluation_group": case["evaluation_group"],
            "vector_primary": case["vector_primary"], "llm_failure_eligible": case["llm_failure_eligible"],
            "low_literal_overlap": bool(case["lexical_overlap"].get("low_literal_overlap"))}


async def rank_dataset(dataset, *, config, provider=None, warm_queries=100):
    """No LLM request. An injected provider is for deterministic mechanism tests."""
    if config.backend not in {"semantic", "hash"}:
        raise ValueError("rank backend must be semantic or hash")
    if warm_queries != 0 and warm_queries < 100:
        raise ValueError("warm measurements require at least 100 queries, or explicit zero to skip")
    provider = provider or IntentEmbeddingProvider(config)
    recognizer = IntentRecognizer("offline-ranking", embedding_provider=provider, calibration={}, clock=lambda: FIXED_CLOCK)
    rows, warm, warm_states = [], [], []
    try:
        started = time.perf_counter()
        await provider.initialize()
        initialization_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        template_error = None
        try:
            await recognizer._load_template_embeddings()
        except Exception as exc:
            template_error = type(exc).__name__
        template_ms = (time.perf_counter() - started) * 1000
        first_query_ms = None
        warm_message = None
        for case in dataset["cases"]:
            row = case_fields(case)
            if detect_emergency(case["message"]):
                row.update(fixed_emergency_bypass=True, ranking=[], top1_prediction="other", embedding=None, query_ms=None)
            else:
                started = time.perf_counter()
                result = await recognizer._embedding_recognize(case["message"])
                elapsed = (time.perf_counter() - started) * 1000
                if first_query_ms is None:
                    first_query_ms = elapsed
                warm_message = case["message"]
                ranks = serializable(result.get("ranking", []))
                row.update(fixed_emergency_bypass=False, ranking=ranks, embedding=serializable(result),
                           top1_prediction=ranks[0]["intent"] if ranks else "other", query_ms=elapsed)
            rows.append(row)
        if warm_message:
            for _ in range(warm_queries):
                started = time.perf_counter()
                measured = await recognizer._embedding_recognize(warm_message)
                warm.append((time.perf_counter() - started) * 1000)
                warm_states.append({"backend": measured.get("info", {}).get("active_backend"),
                                    "failed": bool(measured.get("failed"))})
        status = provider.status()
        quality_valid = (template_error is None and not status.get("failure_count")
            and status.get("active_backend") == config.backend
            and all(not (row.get("embedding") or {}).get("failed")
                    and not (row.get("embedding") or {}).get("info", {}).get("fallback_reason")
                    and (row["fixed_emergency_bypass"] or
                         row["embedding"]["info"].get("active_backend") == config.backend) for row in rows)
            and all(not item["failed"] and item["backend"] == config.backend for item in warm_states))
        return {"schema_version": SCHEMA_VERSION, "kind": "intent_rankings", "status": "candidate",
                "identity": identity(dataset, recognizer), "requested_backend": config.backend,
                "environment": {**environment(), "threads": config.threads, "timeout_ms": config.timeout_ms},
                "provider": status, "rows": rows, "quality_valid": quality_valid,
                "semantic_sample_count": sum((row.get("embedding") or {}).get("info", {}).get("active_backend") == "semantic"
                                             and not (row.get("embedding") or {}).get("failed") for row in rows),
                "metrics": grouped_metrics(rows, "top1_prediction", ranking=True),
                "metrics_by_actual_backend": {backend: grouped_metrics(
                    [row for row in rows if (row.get("embedding") or {}).get("info", {}).get("active_backend") == backend],
                    "top1_prediction", ranking=True) for backend in ("semantic", "hash", "disabled")},
                "errors": [row["id"] for row in rows if not row["fixed_emergency_bypass"]
                           and row["top1_prediction"] != row["expected_intent"]],
                "failures": {"template_error": template_error,
                    "classification_count": sum(bool((row.get("embedding") or {}).get("failed")) for row in rows),
                    "fallback_sample_count": sum(bool((row.get("embedding") or {}).get("info", {}).get("fallback_reason")) for row in rows),
                    "provider_failure_count": status.get("failure_count", 0)},
                "performance": {"model_initialize_ms": initialization_ms, "template_build_ms": template_ms,
                    "first_query_ms": first_query_ms, "warm_queries": timing_summary(warm),
                    "warm_query_rows_ms": warm, "result_cache_hit_ms": None,
                    "warm_actual_backends": dict(Counter(item["backend"] for item in warm_states)),
                    "warm_failures": sum(item["failed"] for item in warm_states),
                    "peak_process_rss_bytes": peak_rss_bytes(), "includes_llm_time": False},
                "limitations": ["Ranking is current-message only; context and OTHER are separate groups.",
                                 "Fallback rows are not real semantic evidence; cache timing is measured in replay."]}
    finally:
        await recognizer.aclose()
        await provider.aclose()


def readable_json(value):
    """Retain an invalid model payload as readable evidence without JSON NaN."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_nonfinite_number": str(value)}
    if isinstance(value, dict):
        return {str(key): readable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [readable_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"unserializable_type": type(value).__name__}


class CaptureClient:
    """Capture exactly the request used by the production parser, without headers."""
    def __init__(self, client, before_request):
        self.client = client
        self.messages = self
        self.request = None
        self.response = None
        self.error_type = None
        self.before_request = before_request

    async def create(self, **kwargs):
        self.request = deepcopy(kwargs)
        await self.before_request(self.request)  # fsync before the external side effect
        try:
            result = await self.client.messages.create(**kwargs)
            try:
                self.response = result.model_dump(mode="json")
                fingerprint(self.response)
            except Exception as exc:
                self.error_type = "ResponseSerializationError"
                self.response = {"serialization_error": type(exc).__name__,
                                 "payload": readable_json(self.response),
                                 "text_content": extract_text_content(getattr(result, "content", []))}
            return result
        except Exception as exc:
            self.error_type = type(exc).__name__
            raise

    async def close(self):
        await self.client.close()


def checkpoint_rows(path, metadata):
    """Strict resume: never silently drop torn, duplicated or mismatched records."""
    path = Path(path)
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or json.loads(lines[0]) != {"type": "metadata", "metadata": metadata}:
        raise ValueError("LLM checkpoint fingerprints or request settings differ")
    rows, started = {}, {}
    for line in lines[1:]:
        entry = json.loads(line)
        if entry.get("type") == "started":
            attempt = entry["attempt"]
            if attempt["id"] in started or attempt["id"] in rows or entry.get("sha256") != fingerprint(attempt):
                raise ValueError("invalid or duplicate started LLM record")
            started[attempt["id"]] = attempt
            continue
        if entry.get("type") != "completed" or entry["row"]["id"] in rows:
            raise ValueError("invalid or duplicate LLM checkpoint record")
        row = entry["row"]
        if entry.get("sha256") != fingerprint(row):
            raise ValueError("LLM checkpoint row fingerprint differs")
        attempt = started.pop(row["id"], None)
        if row["request"] is not None and (attempt is None or
                attempt["request_sha256"] != row["request_sha256"] or
                attempt["input_sha256"] != row["input_sha256"]):
            raise ValueError("completed LLM record has no matching started request")
        rows[row["id"]] = row
    if started:
        raise ValueError("uncertain LLM requests; automatic resend blocked for IDs: " + ", ".join(sorted(started)))
    return rows


def build_failure_fixture(dataset, rankings):
    """Dev-only injected failure for threshold selection, never fusion evidence."""
    if dataset["split"] != "dev":
        raise ValueError("failure fixture is reserved for development calibration")
    if any(rankings["identity"].get(key) != value for key, value in dataset["identity"].items()):
        raise ValueError("failure fixture ranking dataset identity differs")
    rows = []
    for case in dataset["cases"]:
        bypass = bool(detect_emergency(case["message"]))
        rows.append({"id": case["id"], "input_sha256": fingerprint(input_for(case)),
                     "bypassed_emergency": bypass,
                     "parsed": None if bypass else {"intent": "other", "confidence": 0.0,
                         "failed": True, "reasoning": "injected failure for development calibration"},
                     "request": None, "response": None, "request_sha256": None, "response_sha256": None,
                     "elapsed_ms": None, "error_type": None if bypass else "InjectedFailure"})
    return {"schema_version": SCHEMA_VERSION, "kind": "intent_llm_responses",
            "metadata": {"identity": deepcopy(rankings["identity"]), "provenance": "injected_failure"},
            "rows": rows, "raw_sha256": fingerprint(rows), "model_requests": 0,
            "failures": sum(not row["bypassed_emergency"] for row in rows),
            "notes": "No model invoked. Use llm_failure metrics only; not real fusion quality evidence."}


async def collect_llm(dataset, *, checkpoint, api_key, base_url, model, timeout=60.0,
                      concurrency=1, client_factory=AsyncAnthropic, provenance="live_sdk"):
    if concurrency not in {1, 2, 3} or timeout <= 0:
        raise ValueError("LLM concurrency must be 1..3 and timeout positive")
    config = EmbeddingConfig(backend="disabled", fallback="disabled")
    provider = IntentEmbeddingProvider(config)
    probe = IntentRecognizer("fingerprint-only", embedding_provider=provider, calibration={})
    try:
        metadata = {"schema_version": SCHEMA_VERSION, "checkpoint_protocol": "started-completed-v1",
                    "identity": identity(dataset, probe),
                    "provenance": provenance, "request_settings": {"model": model, "max_tokens": 256,
                        "temperature": .1, "options": llm_request_options(), "max_retries": 0,
                        "timeout_seconds": timeout, "base_url_sha256": fingerprint(base_url)},
                    "parser_sha256": fingerprint(inspect.getsource(IntentRecognizer._llm_recognize))}
    finally:
        await probe.aclose()
        await provider.aclose()
    rows = checkpoint_rows(checkpoint, metadata)
    cases = {case["id"]: case for case in dataset["cases"]}
    if set(rows) - cases.keys():
        raise ValueError("checkpoint contains unknown IDs")
    for case_id, row in rows.items():
        if row["input_sha256"] != fingerprint(input_for(cases[case_id])):
            raise ValueError("checkpoint input fingerprint differs")
    checkpoint = Path(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    lock, gate = asyncio.Lock(), asyncio.Semaphore(concurrency)
    with checkpoint.open("a", encoding="utf-8", newline="\n") as stream:
        def append(value):
            stream.write(json.dumps(serializable(value), ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if stream.tell() == 0:
            append({"type": "metadata", "metadata": metadata})

        async def one(case):
            async with gate:
                if detect_emergency(case["message"]):
                    row = {"id": case["id"], "input_sha256": fingerprint(input_for(case)),
                           "bypassed_emergency": True, "parsed": None, "request": None, "response": None,
                           "request_sha256": None, "response_sha256": None, "elapsed_ms": None, "error_type": None}
                else:
                    local_provider = IntentEmbeddingProvider(config)
                    recognizer = IntentRecognizer("replaced-client", model=model, embedding_provider=local_provider, calibration={})
                    await recognizer.client.close()
                    async def before_request(request):
                        attempt = {"id": case["id"], "attempt": 1,
                                   "input_sha256": fingerprint(input_for(case)),
                                   "request_sha256": fingerprint(request), "request": request}
                        async with lock:
                            append({"type": "started", "attempt": attempt, "sha256": fingerprint(attempt)})
                    client = CaptureClient(client_factory(api_key=api_key, base_url=base_url,
                                                          max_retries=0, timeout=timeout), before_request)
                    recognizer.client = client
                    started = time.perf_counter()
                    try:
                        parsed = await recognizer._llm_recognize(**input_for(case))
                        unvalidated = None
                        try:
                            fingerprint(parsed)
                            confidence = float(parsed.get("confidence", 0.0))
                            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                                raise ValueError("invalid confidence")
                            if client.error_type == "ResponseSerializationError":
                                raise ValueError("invalid raw serialization")
                        except (ValueError, TypeError):
                            unvalidated = readable_json(parsed)
                            parsed = {"intent": IntentCategory.OTHER, "confidence": 0.0, "failed": True,
                                      "reasoning": "Invalid model response preserved in raw evidence"}
                            client.error_type = client.error_type or "InvalidParsedResponse"
                        row = {"id": case["id"], "input_sha256": fingerprint(input_for(case)),
                               "bypassed_emergency": False, "parsed": serializable(parsed),
                               "parsed_unvalidated": unvalidated,
                               "request": client.request, "response": client.response,
                               "request_sha256": fingerprint(client.request), "response_sha256": fingerprint(client.response),
                               "elapsed_ms": (time.perf_counter() - started) * 1000, "error_type": client.error_type}
                    finally:
                        await recognizer.aclose()
                        await local_provider.aclose()
                async with lock:
                    append({"type": "completed", "row": row, "sha256": fingerprint(row)})
                    rows[case["id"]] = row
        tasks = [asyncio.create_task(one(case)) for case in dataset["cases"] if case["id"] not in rows]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    ordered = [rows[case["id"]] for case in dataset["cases"]]
    return {"schema_version": SCHEMA_VERSION, "kind": "intent_llm_responses", "metadata": metadata,
            "rows": ordered, "raw_sha256": fingerprint(ordered),
            "failures": sum(bool((row["parsed"] or {}).get("failed")) for row in ordered),
            "model_requests": sum(row["request"] is not None for row in ordered),
            "actual_response_models": dict(Counter(row["response"].get("model", "unavailable")
                for row in ordered if isinstance(row["response"], dict))),
            "notes": "Failures remain completed records; started without completed is uncertain and blocks automatic resend."}


class ReplayProvider:
    """Read-only captured state; attempting real encoding during replay is an error."""
    def __init__(self, backend):
        self.state = {"configured_backend": backend, "active_backend": backend,
                      "space_id": backend, "generation": 0, "status": "replay", "fallback_reason": None}

    def status(self):
        return dict(self.state)

    async def initialize(self):
        return self.status()

    async def encode_batch(self, texts):
        raise AssertionError("replay must not encode or call a model")


def parsed_result(value):
    result = deepcopy(value)
    result["intent"] = IntentCategory(result["intent"])
    return result


def validate_artifact(dataset, artifact_identity, recognizer):
    if artifact_identity != identity(dataset, recognizer):
        raise ValueError("artifact data/template/pattern/source fingerprints differ")


async def replay_dataset(dataset, *, rankings, llm, calibration, backend):
    provider = ReplayProvider(backend)
    recognizer = IntentRecognizer("offline-replay", embedding_provider=provider,
                                  calibration=calibration, clock=lambda: FIXED_CLOCK)
    rows, cache_ms = [], []
    try:
        validate_artifact(dataset, llm["metadata"]["identity"], recognizer)
        if fingerprint(llm["rows"]) != llm["raw_sha256"]:
            raise ValueError("raw LLM response fingerprint differs")
        if backend != "disabled":
            if rankings["requested_backend"] != backend:
                raise ValueError("requested replay backend differs from rankings")
            validate_artifact(dataset, rankings["identity"], recognizer)
        raw_by_id = {row["id"]: row for row in llm["rows"]}
        rank_by_id = {row["id"]: row for row in rankings["rows"]} if rankings else {}
        if len(raw_by_id) != len(llm["rows"]) or set(raw_by_id) != {case["id"] for case in dataset["cases"]}:
            raise ValueError("LLM output must contain every dataset ID exactly once")
        if backend != "disabled" and (len(rank_by_id) != len(rankings["rows"]) or set(rank_by_id) != set(raw_by_id)):
            raise ValueError("ranking output must contain every dataset ID")
        for case in dataset["cases"]:
            raw = raw_by_id[case["id"]]
            if raw["input_sha256"] != fingerprint(input_for(case)):
                raise ValueError("raw LLM input fingerprint differs")
            if raw["bypassed_emergency"] != bool(detect_emergency(case["message"])):
                raise ValueError("LLM emergency bypass record differs from fixed rule")
            embedding = {"intent": IntentCategory.OTHER, "confidence": 0.0,
                         "info": {"active_backend": "disabled", "status": "disabled_explicit"}}
            if backend != "disabled":
                rank = rank_by_id[case["id"]]
                if rank["input_sha256"] != fingerprint(input_for(case)):
                    raise ValueError("ranking input fingerprint differs")
                captured = rank.get("embedding")
                if captured:
                    provider.state.update(captured["info"])
                    if captured.get("failed"):
                        embedding = parsed_result(captured)
                    else:
                        embedding = recognizer._apply_embedding_calibration(
                            case["message"], rank["ranking"], {**captured["info"], "elapsed_ms": None})
            async def vector(_message):
                return deepcopy(embedding)
            async def frozen_llm(_message, _history):
                if raw["parsed"] is None:
                    raise AssertionError("emergency bypass unexpectedly called LLM")
                return parsed_result(raw["parsed"])
            recognizer._embedding_recognize = vector
            recognizer._llm_recognize = frozen_llm
            recognizer._cache.clear()
            result = await recognizer.recognize(case["message"], case["history"])
            if not raw["bypassed_emergency"] and not (raw["parsed"] or {}).get("failed") and recognizer._cache:
                started = time.perf_counter()
                cached = await recognizer.recognize(case["message"], case["history"])
                if cached.embedding_info.get("cache_hit"):
                    cache_ms.append((time.perf_counter() - started) * 1000)
            async def failed_llm(_message, _history):
                return {"intent": IntentCategory.OTHER, "confidence": 0.0, "failed": True, "reasoning": "injected failure"}
            recognizer._llm_recognize = failed_llm
            recognizer._cache.clear()
            failed = await recognizer.recognize(case["message"], case["history"])
            row = {**case_fields(case), "prediction": result.intent.value, "confidence": result.confidence,
                   "source_scores": result.source_scores, "embedding_info": result.embedding_info,
                   "failure_prediction": failed.intent.value, "failure_confidence": failed.confidence,
                   "llm_failed": bool((raw["parsed"] or {}).get("failed")), "raw_response_sha256": raw["response_sha256"],
                   "embedding_failed": bool(embedding.get("failed") or result.embedding_info.get("classification_error"))}
            rows.append(row)
        quality_valid = (backend == "disabled" or bool(rankings.get("quality_valid"))) and all(
            not row["embedding_failed"] and not row["embedding_info"].get("fallback_reason") for row in rows)
        return {"schema_version": SCHEMA_VERSION, "kind": "intent_fusion_replay", "status": "candidate",
                "identity": identity(dataset, recognizer), "backend": backend,
                "calibration": calibration, "calibration_sha256": fingerprint(calibration),
                "llm_provenance": llm["metadata"]["provenance"], "raw_llm_sha256": llm["raw_sha256"],
                "ranking_sha256": fingerprint(rankings) if rankings else None,
                "rows": rows, "metrics": grouped_metrics(rows, "prediction"), "llm_failure": selective_metrics(rows),
                "quality_valid": quality_valid,
                "fusion_quality_valid": quality_valid and llm["metadata"]["provenance"] == "live_sdk"
                    and not any(row["llm_failed"] for row in rows),
                "metrics_by_actual_backend": {actual: grouped_metrics(
                    [row for row in rows if row["embedding_info"].get("active_backend") == actual], "prediction")
                    for actual in ("semantic", "hash", "disabled")},
                "semantic_sample_count": sum(row["embedding_info"].get("active_backend") == "semantic"
                    and not row["embedding_failed"] for row in rows),
                "errors": [row["id"] for row in rows if row["prediction"] != row["expected_intent"]],
                "observed_llm_failures": sum(row["llm_failed"] for row in rows),
                "fallback_samples": sum(bool(row["embedding_info"].get("fallback_reason")) for row in rows),
                "performance": {"result_cache_hit": timing_summary(cache_ms), "model_inference_during_replay": False},
                "limitations": ["Injected LLM failure measures mechanism on frozen inputs, not real outage frequency.",
                                 "No baseline is accepted automatically; fake LLM provenance is not real fusion evidence."]}
    finally:
        await recognizer.aclose()


async def replay_grid(dataset, *, rankings, llm, calibration, backend, combinations):
    if dataset["split"] != "dev":
        raise ValueError("threshold grid is allowed only on the development split")
    if backend not in {"semantic", "hash"} or not 1 <= len(combinations) <= 25:
        raise ValueError("grid must contain 1..25 combinations for semantic/hash")
    results = []
    for index, combination in enumerate(combinations):
        if set(combination) != {"min_score", "min_margin"} or any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in combination.values()):
            raise ValueError("each grid entry needs finite min_score and min_margin")
        policy = deepcopy(calibration)
        policy["backends"][backend].update(combination)
        report = await replay_dataset(dataset, rankings=rankings, llm=llm, calibration=policy, backend=backend)
        results.append({"index": index, "thresholds": combination, "report": report})
    return {"schema_version": SCHEMA_VERSION, "kind": "intent_threshold_grid", "status": "candidate",
            "backend": backend, "combinations_sha256": fingerprint(combinations), "results": results,
            "model_inference_during_replay": False}


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("rank", "collect-llm", "replay"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", required=True, type=Path)
        command.add_argument("--manifest", type=Path)
        command.add_argument("--allow-holdout", action="store_true")
        command.add_argument("--output", required=True, type=Path)
        if name == "rank":
            command.add_argument("--backend", choices=("hash", "semantic"), required=True)
            command.add_argument("--warm-queries", type=int, default=100)
        elif name == "collect-llm":
            command.add_argument("--checkpoint", required=True, type=Path)
            command.add_argument("--timeout", type=float, default=60.0)
            command.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=1)
        else:
            command.add_argument("--backend", choices=("hash", "semantic", "disabled"), required=True)
            command.add_argument("--rankings", type=Path)
            command.add_argument("--llm", type=Path, required=True)
            command.add_argument("--calibration", type=Path, required=True)
            command.add_argument("--grid", type=Path)
    return value


async def run_cli(args):
    if args.output.exists():
        raise ValueError("output already exists; choose a new candidate path")
    dataset = load_dataset(args.dataset, manifest_path=args.manifest, allow_holdout=args.allow_holdout)
    if args.command == "rank":
        config = replace(EmbeddingConfig.from_env(), backend=args.backend)
        result = await rank_dataset(dataset, config=config, warm_queries=args.warm_queries)
    elif args.command == "collect-llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY must be supplied through the environment")
        result = await collect_llm(dataset, checkpoint=args.checkpoint, api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            model=os.getenv("ANTHROPIC_MODEL", "deepseek-flash"), timeout=args.timeout, concurrency=args.concurrency)
    else:
        if args.backend != "disabled" and not args.rankings:
            raise ValueError("semantic/hash replay requires --rankings")
        kwargs = {"rankings": read_json(args.rankings) if args.rankings else None,
                  "llm": read_json(args.llm), "calibration": read_json(args.calibration), "backend": args.backend}
        if args.grid:
            result = await replay_grid(dataset, **kwargs, combinations=read_json(args.grid))
        else:
            result = await replay_dataset(dataset, **kwargs)
    write_json(args.output, result)
    print(json.dumps({"output": str(args.output), "kind": result["kind"], "status": "candidate"}))


def main():
    asyncio.run(run_cli(parser().parse_args()))


if __name__ == "__main__":
    main()
