"""Synthetic executor checks only: no held-out examples, remote LLM or ONNX."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.intent_embeddings import EmbeddingConfig, HASH_SPACE_ID, IntentEmbeddingProvider
from core.intent_recognizer import IntentRecognizer
from evaluation import semantic_intent as runner


def dataset(tmp_path, split="dev"):
    messages = [("医院几点开门？", "hospital_info", "single_turn"),
                ("请把门诊取号的先后顺序说清楚", "visit_process", "single_turn"),
                ("这句话尚未包含请求", "other", "ambiguous"),
                ("有人现在失去意识", "emergency", "emergency")]
    cases = [{"id": f"{split}:{index}", "message": message, "expected_intent": label,
              "history": [], "evaluation_group": group, "vector_primary": group == "single_turn",
              "llm_failure_eligible": group == "single_turn",
              "lexical_overlap": {"low_literal_overlap": index == 0}, "label_reason": "SECRET_GOLD_REASON"}
             for index, (message, label, group) in enumerate(messages)]
    path = tmp_path / f"{split}.json"
    path.write_text(json.dumps({"split": split, "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return runner.load_dataset(path) if split != "holdout" else path


class FakeClient:
    calls = []
    options = []
    fail = False

    def __init__(self, **kwargs):
        self.options.append(kwargs)
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("must not be written to evidence")
        response = {"content": [{"type": "text", "text": json.dumps({
            "intent": "hospital_info", "confidence": .9, "reasoning": "fixed fixture"})}],
            "model": "test-model", "usage": {"input_tokens": 20, "output_tokens": 10}}
        return SimpleNamespace(content=response["content"], model_dump=lambda **_: response)

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def reset_fake(monkeypatch):
    FakeClient.calls = []
    FakeClient.options = []
    FakeClient.fail = False
    monkeypatch.delenv("MEDIPET_THINKING", raising=False)


def collect(data, tmp_path, **kwargs):
    return asyncio.run(runner.collect_llm(data, checkpoint=tmp_path / "raw.jsonl", api_key="fake-test-key",
        base_url="https://fixture.invalid", model="test-model", client_factory=FakeClient,
        provenance="deterministic_fixture", **kwargs))


def ranked(data):
    return asyncio.run(runner.rank_dataset(data, config=EmbeddingConfig(backend="hash"), warm_queries=0))


def calibration(rank):
    return {"schema_version": 1, "template_fingerprint": rank["identity"]["template_fingerprint"],
            "pattern": {"rule_fingerprint": rank["identity"]["pattern_fingerprint"], "min_score": .75},
            "backends": {"hash": {"space_id": HASH_SPACE_ID, "min_score": .99, "min_margin": .99},
                         "semantic": {"space_id": "fixture:semantic", "min_score": .5, "min_margin": .1}}}


def test_metrics_count_other_and_top3_with_hand_calculated_confusion():
    result = runner.classification_metrics(["a", "a", "b", "other"], ["a", "other", "b", "a"],
                                          [["a"], ["b", "a"], ["b"], ["a"]])
    assert result["accuracy"] == .5
    assert result["macro_f1"] == pytest.approx(.5)  # (a=.5, b=1, OTHER=0) / 3
    assert result["top3_correct"] == 3
    assert result["confusion"]["other"] == {"a": 1}
    assert result["other_count"] == 1
    assert runner.classification_metrics([], [])["accuracy"] is None


def test_selective_denominator_cannot_pass_by_abstaining():
    rows = [{"id": str(index), "expected_intent": gold, "failure_prediction": pred,
             "llm_failure_eligible": eligible} for index, (gold, pred, eligible) in enumerate([
        ("a", "a", True), ("b", "a", True), ("a", "other", True), ("other", "other", False)])]
    result = runner.selective_metrics(rows)
    assert result["accuracy"] == .5 and result["coverage"] == pytest.approx(2 / 3)
    assert result["wrongly_accepted_ids"] == ["1"] and result["rejected_ids"] == ["2"]
    for row in rows:
        row["failure_prediction"] = "other"
    assert runner.selective_metrics(rows)["accuracy"] is None
    assert runner.selective_metrics(rows)["coverage"] == 0


def test_holdout_requires_explicit_approved_fingerprint(tmp_path):
    path = dataset(tmp_path, "holdout")
    with pytest.raises(ValueError, match="holdout requires"):
        runner.load_dataset(path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"review": {"status": "approved"},
                                  "file_sha256": {path.name: runner.file_hash(path)}}))
    assert runner.load_dataset(path, allow_holdout=True, manifest_path=manifest)["split"] == "holdout"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen manifest"):
        runner.load_dataset(path, allow_holdout=True, manifest_path=manifest)


def test_rank_never_calls_llm_and_separates_boundaries(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("LLM was called")
    monkeypatch.setattr(IntentRecognizer, "_llm_recognize", forbidden)
    report = ranked(dataset(tmp_path))
    assert report["metrics"]["primary"]["count"] == 2
    assert report["metrics"]["ambiguous"]["count"] == 1
    assert report["metrics"]["emergency"]["fixed_rule_excluded"] == 1
    assert report["provider"]["active_backend"] == "hash"
    assert report["performance"]["warm_queries"]["count"] == 0
    assert report["performance"]["result_cache_hit_ms"] is None


def test_collector_records_exact_requests_and_resumes_only_missing(tmp_path):
    data = dataset(tmp_path)
    report = collect(data, tmp_path)
    assert len(FakeClient.calls) == 3  # emergency bypass never requests a model
    assert all(options["max_retries"] == 0 and options["timeout"] == 60 for options in FakeClient.options)
    for call in FakeClient.calls:
        assert "SECRET_GOLD_REASON" not in json.dumps(call)
        assert set(call) == {"model", "max_tokens", "temperature", "messages"}
    assert report["rows"][0]["request_sha256"] == runner.fingerprint(FakeClient.calls[0])
    assert collect(data, tmp_path)["raw_sha256"] == report["raw_sha256"]
    assert len(FakeClient.calls) == 3
    checkpoint = tmp_path / "raw.jsonl"
    checkpoint.write_text("\n".join(checkpoint.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
    resumed = collect(data, tmp_path)
    assert len(resumed["rows"]) == 4 and len(FakeClient.calls) == 3  # missing emergency row only
    lines = checkpoint.read_text(encoding="utf-8").splitlines()
    remaining = [line for line in lines if (json.loads(line).get("attempt") or
        json.loads(line).get("row") or {}).get("id") != "dev:0"]
    checkpoint.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    collect(data, tmp_path)
    assert len(FakeClient.calls) == 4  # exactly the deleted model response is recollected
    entries = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines()]
    started = next(i for i, entry in enumerate(entries) if entry["type"] == "started" and entry["attempt"]["id"] == "dev:0")
    completed = next(i for i, entry in enumerate(entries) if entry["type"] == "completed" and entry["row"]["id"] == "dev:0")
    assert started < completed


def test_collector_keeps_failure_without_automatic_retry(tmp_path):
    FakeClient.fail = True
    data = dataset(tmp_path)
    report = collect(data, tmp_path)
    assert report["failures"] == 3
    assert all(row["error_type"] == "TimeoutError" for row in report["rows"][:3])
    assert "must not be written" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    collect(data, tmp_path)
    assert len(FakeClient.calls) == 3


@pytest.mark.parametrize("change", ["model", "input", "thinking"])
def test_resume_refuses_changed_fingerprints(tmp_path, monkeypatch, change):
    data = dataset(tmp_path)
    collect(data, tmp_path)
    kwargs = {}
    if change == "model":
        kwargs["timeout"] = 61
    elif change == "input":
        data["cases"][0]["message"] += " changed"
    else:
        monkeypatch.setenv("MEDIPET_THINKING", "disabled")
    with pytest.raises(ValueError, match="fingerprint|settings"):
        collect(data, tmp_path, **kwargs)
    assert len(FakeClient.calls) == 3


def test_checkpoint_refuses_torn_or_corrupted_row(tmp_path):
    data = dataset(tmp_path)
    collect(data, tmp_path)
    path = tmp_path / "raw.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":')
    with pytest.raises(json.JSONDecodeError):
        collect(data, tmp_path)


def test_replay_uses_actual_vote_and_same_raw_across_modes(tmp_path, monkeypatch):
    data = dataset(tmp_path)
    ranks = ranked(data)
    raw = collect(data, tmp_path)
    calls = 0
    actual = IntentRecognizer._vote
    def counted(self, *args):
        nonlocal calls
        calls += 1
        return actual(self, *args)
    monkeypatch.setattr(IntentRecognizer, "_vote", counted)
    first = asyncio.run(runner.replay_dataset(data, rankings=ranks, llm=raw, calibration=calibration(ranks), backend="hash"))
    disabled = asyncio.run(runner.replay_dataset(data, rankings=None, llm=raw, calibration=calibration(ranks), backend="disabled"))
    assert calls == 12  # 3 nonemergency inputs × normal/failure × 2 modes
    assert first["raw_llm_sha256"] == disabled["raw_llm_sha256"]
    assert first["llm_provenance"] == "deterministic_fixture"
    assert first["metrics"]["all"]["count"] == 4
    assert first["rows"][3]["prediction"] == "emergency"
    assert first["performance"]["model_inference_during_replay"] is False
    assert len(FakeClient.calls) == 3


def test_grid_reuses_raw_ranking_and_refuses_holdout_or_oversized_search(tmp_path):
    data = dataset(tmp_path)
    ranks, raw = ranked(data), collect(data, tmp_path)
    combos = [{"min_score": .2, "min_margin": .01}, {"min_score": .99, "min_margin": .99}]
    result = asyncio.run(runner.replay_grid(data, rankings=ranks, llm=raw, calibration=calibration(ranks),
                                          backend="hash", combinations=combos))
    assert len(result["results"]) == 2 and len(FakeClient.calls) == 3
    assert len({item["report"]["raw_llm_sha256"] for item in result["results"]}) == 1
    for bad_data, bad_combos in (({**data, "split": "holdout"}, combos), (data, combos * 13)):
        with pytest.raises(ValueError):
            asyncio.run(runner.replay_grid(bad_data, rankings=ranks, llm=raw, calibration=calibration(ranks),
                                           backend="hash", combinations=bad_combos))


def test_replay_refuses_source_or_response_mismatch(tmp_path):
    data = dataset(tmp_path)
    ranks, raw = ranked(data), collect(data, tmp_path)
    raw["rows"][0]["parsed"]["confidence"] = 1.0
    with pytest.raises(ValueError, match="response fingerprint"):
        asyncio.run(runner.replay_dataset(data, rankings=ranks, llm=raw, calibration=calibration(ranks), backend="hash"))
    raw["raw_sha256"] = runner.fingerprint(raw["rows"])
    raw["metadata"]["identity"]["source_sha256"]["core/intent_recognizer.py"] = "wrong"
    with pytest.raises(ValueError, match="source fingerprints"):
        asyncio.run(runner.replay_dataset(data, rankings=ranks, llm=raw, calibration=calibration(ranks), backend="hash"))


def test_report_publication_does_not_overwrite_a_candidate(tmp_path):
    path = tmp_path / "candidate.json"
    runner.write_json(path, {"status": "candidate"})
    with pytest.raises(ValueError, match="already exists"):
        runner.write_json(path, {"status": "accepted"})
    assert runner.read_json(path) == {"status": "candidate"}


def test_semantic_replay_and_dev_failure_fixture_keep_provenance(tmp_path):
    data = dataset(tmp_path)
    ranks = ranked(data)
    ranks["requested_backend"] = "semantic"
    for row in ranks["rows"]:
        if row["embedding"]:
            row["embedding"]["info"].update(configured_backend="semantic", active_backend="semantic",
                                           space_id="fixture:semantic", generation=1)
    raw = runner.build_failure_fixture(data, ranks)
    assert raw["model_requests"] == 0 and raw["metadata"]["provenance"] == "injected_failure"
    result = asyncio.run(runner.replay_dataset(data, rankings=ranks, llm=raw,
                        calibration=calibration(ranks), backend="semantic"))
    assert result["llm_failure"]["eligible"] == 2
    assert result["rows"][0]["embedding_info"]["active_backend"] == "semantic"
    assert result["rows"][0]["embedding_info"]["status"] != "stale_snapshot"
    assert result["llm_provenance"] == "injected_failure"
    with pytest.raises(ValueError, match="development"):
        runner.build_failure_fixture({**data, "split": "holdout"}, ranks)


def test_started_without_completed_blocks_automatic_resend(tmp_path):
    data = dataset(tmp_path)
    class InterruptedClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            # Assert the write-ahead record is visible before the SDK call begins.
            entries = [json.loads(line) for line in (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
            assert entries[-1]["type"] == "started"
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.collect_llm(data, checkpoint=tmp_path / "raw.jsonl", api_key="fake-test-key",
            base_url="https://fixture.invalid", model="test-model", client_factory=InterruptedClient,
            provenance="deterministic_fixture"))
    calls = len(FakeClient.calls)
    with pytest.raises(ValueError, match="uncertain LLM requests; automatic resend blocked"):
        collect(data, tmp_path)
    assert len(FakeClient.calls) == calls


@pytest.mark.parametrize("mode", ["nan", "unserializable"])
def test_invalid_response_remains_completed_readable_failure(tmp_path, mode):
    data = dataset(tmp_path)
    class InvalidClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            response = {"content": [{"type": "text", "text": '{"intent":"hospital_info","confidence":NaN}'}]}
            def dump(**kwargs):
                if mode == "unserializable":
                    raise TypeError("cannot serialize response")
                return response
            return SimpleNamespace(content=response["content"], model_dump=dump)
    report = asyncio.run(runner.collect_llm(data, checkpoint=tmp_path / "raw.jsonl", api_key="fake-test-key",
        base_url="https://fixture.invalid", model="test-model", client_factory=InvalidClient,
        provenance="deterministic_fixture"))
    assert report["failures"] == 3 and report["model_requests"] == 3
    assert all(row["parsed"]["confidence"] == 0 for row in report["rows"][:3])
    assert report["rows"][0]["response"] is not None
    resumed = collect(data, tmp_path)
    assert resumed["raw_sha256"] == report["raw_sha256"] and len(FakeClient.calls) == 3


def test_semantic_fallback_is_observable_and_invalidates_quality(tmp_path):
    class MissingSemantic:
        model, dimension, space_id = "fixture-only", 512, "fixture:missing"
        def initialize(self):
            raise FileNotFoundError("synthetic missing model")
    data = dataset(tmp_path)
    config = EmbeddingConfig(backend="semantic", fallback="hash")
    provider = IntentEmbeddingProvider(config, semantic_backend=MissingSemantic())
    ranks = asyncio.run(runner.rank_dataset(data, config=config, provider=provider, warm_queries=0))
    assert ranks["quality_valid"] is False and ranks["semantic_sample_count"] == 0
    assert ranks["metrics_by_actual_backend"]["hash"]["all"]["count"] == 3
    assert ranks["failures"]["fallback_sample_count"] == 3
    raw = runner.build_failure_fixture(data, ranks)
    replay = asyncio.run(runner.replay_dataset(data, rankings=ranks, llm=raw,
        calibration=calibration(ranks), backend="semantic"))
    assert replay["quality_valid"] is False and replay["fusion_quality_valid"] is False
    assert replay["metrics_by_actual_backend"]["hash"]["primary"]["count"] == 2
