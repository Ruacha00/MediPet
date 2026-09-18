"""Deterministic contract/scoring tests; no live model or retrieval calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.answer_quality import (
    ANSWER_SYSTEM, ARMS, DIMENSIONS, Journal, aggregate, answer_input, fingerprint,
    judgment_passed, load_cases, parse_judgment, run_comparison, sdk_invoker,
    validate_retrievals,
)


def sample_case(identifier="one", group="answerable"):
    return {"id": identifier, "group": group, "question": "诊区位于哪层？", "source_doc_ids": ["location"],
            "required_facts": ["诊区在二层。"], "forbidden_claims": ["诊区在三层。"],
            "expected_behavior": "clarify_or_abstain" if group == "no_evidence" else "answer"}


def sample_retrieval():
    return {"success": True, "results": [{"content": "诊区在二层。", "doc_id": "location", "chunk_id": "location:0",
                                           "source_id": "public", "source": "knowledge/location.md", "score": .9}],
            "metadata": {"algorithm": "test"}}


def inputs(cases=None):
    cases = cases or [sample_case()]
    dataset = {"schema_version": 1, "dataset_id": "offline-test", "cases": cases}
    retrievals = {"schema_version": 1, "arms": {arm: {case["id"]: sample_retrieval() for case in cases} for arm in ARMS}}
    corpus = {"location": {"doc_id": "location", "content": "诊区在二层。", "sha256": "fixture-source"}}
    return dataset, retrievals, corpus


def good_judgment(case=None, evidence=None):
    case = case or sample_case()
    evidence = evidence if evidence is not None else answer_input(case, sample_retrieval())["evidence"]
    refusing = case["expected_behavior"] == "clarify_or_abstain"
    return {"scores": {dim: 1.0 for dim in DIMENSIONS}, "reasons": {dim: "依据本轮证据和问题逐项核对。" for dim in DIMENSIONS},
            "fact_checks": [{"fact_index": index, "covered": True, "reason": "已覆盖。"} for index in range(len(case["required_facts"]))],
            "support": [{"claim": "诊区在二层。", "evidence_id": evidence[0]["evidence_id"], "quote": evidence[0]["content"]}] if evidence else [],
            "unsupported_claims": [], "abstention": {"needed": refusing, "observed": refusing, "appropriate": True, "reason": "与资料范围一致。"}}


class FakeModel:
    def __init__(self):
        self.calls = []

    async def __call__(self, system, payload, stage):
        self.calls.append((system, deepcopy(payload), stage))
        if stage == "answer":
            return {"text": "诊区在二层。[E1]", "raw": {"id": "fake-answer", "content": "原始回答"}, "usage": {"input_tokens": 5}, "stop_reason": "end_turn"}
        case = {**sample_case(), **payload["reference"]}
        return {"text": json.dumps(good_judgment(case, payload["evidence"]), ensure_ascii=False), "raw": {"id": "fake-judge"}, "stop_reason": "end_turn"}


def run(tmp_path, dataset=None, retrievals=None, corpus=None, model=None):
    d, r, c = inputs()
    return asyncio.run(run_comparison(dataset or d, retrievals or r, corpus or c, output=tmp_path,
                                     invoke=model or FakeModel(), execute=True))


def test_frozen_dataset_covers_all_24_documents_and_negative_controls():
    dataset, corpus = load_cases()
    assert len(dataset["cases"]) == 30
    assert len(corpus) == 24
    assert {doc for case in dataset["cases"] for doc in case["source_doc_ids"]} == set(corpus)
    assert sum(case["group"] == "cross_document" for case in dataset["cases"]) == 2
    assert sum(case["group"] == "no_evidence" for case in dataset["cases"]) == 4
    assert all(len(case["source_doc_ids"]) > 1 for case in dataset["cases"] if case["group"] == "cross_document")


def test_answer_input_cannot_leak_labels_groups_arm_or_retrieval_metadata():
    case = sample_case()
    case.update(required_facts=["GOLD_SENTINEL"], forbidden_claims=["FORBIDDEN_SENTINEL"], private="PRIVATE")
    retrieval = sample_retrieval()
    retrieval["metadata"] = {"expected": "METADATA_SENTINEL"}
    retrieval["results"][0].update(required_facts=["CHUNK_LABEL_SENTINEL"], score=.999, arm="improved")
    payload = answer_input(case, retrieval)
    encoded = json.dumps(payload)
    assert set(payload) == {"question", "evidence"}
    assert all(value not in encoded for value in ["GOLD_SENTINEL", "FORBIDDEN_SENTINEL", "PRIVATE", "METADATA_SENTINEL", "CHUNK_LABEL_SENTINEL", "improved", "score"])
    assert payload["evidence"][0]["evidence_id"] == "E1"


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.1, 1.1, "1"])
def test_judge_rejects_invalid_or_nonfinite_scores(bad):
    judgment = good_judgment()
    judgment["scores"]["correctness"] = bad
    with pytest.raises(ValueError, match="invalid score"):
        parse_judgment(json.dumps(judgment), sample_case(), answer_input(sample_case(), sample_retrieval())["evidence"])


@pytest.mark.parametrize("mutation", ["missing_facts", "duplicate_facts", "invented_quote", "reference_as_retrieval", "missing_reason", "missing_abstention"])
def test_judge_response_requires_complete_auditable_basis(mutation):
    case = sample_case()
    judgment = good_judgment(case)
    if mutation == "missing_facts": judgment["fact_checks"] = []
    if mutation == "duplicate_facts": judgment["fact_checks"][0]["fact_index"] = 7
    if mutation == "invented_quote": judgment["support"][0]["quote"] = "诊区在三层。"
    if mutation == "reference_as_retrieval": judgment["support"][0]["evidence_id"] = "reference-document"
    if mutation == "missing_reason": judgment["reasons"]["correctness"] = ""
    if mutation == "missing_abstention": judgment["abstention"] = {}
    with pytest.raises(ValueError):
        parse_judgment(json.dumps(judgment), case, answer_input(case, sample_retrieval())["evidence"])


def test_high_scores_do_not_override_unsupported_claim_or_missing_requirement():
    case = sample_case()
    judgment = good_judgment(case)
    assert judgment_passed(judgment, case)
    judgment["unsupported_claims"] = [{"claim": "已经替你挂号", "reason": "没有业务执行证据"}]
    assert not judgment_passed(judgment, case)
    judgment["unsupported_claims"] = []
    judgment["fact_checks"][0]["covered"] = False
    assert not judgment_passed(judgment, case)


def test_no_evidence_case_requires_observed_appropriate_abstention():
    case = sample_case(group="no_evidence")
    judgment = good_judgment(case, [])
    assert judgment_passed(judgment, case)
    judgment["abstention"]["observed"] = False
    assert not judgment_passed(judgment, case)
    judgment["abstention"].update(observed=True, appropriate=False)
    assert not judgment_passed(judgment, case)


def test_paired_run_keeps_raw_answers_evidence_judge_background_and_candidate(tmp_path):
    model = FakeModel()
    report = run(tmp_path, model=model)
    assert len(model.calls) == 4
    assert report["complete"] is True
    assert report["status"] == "candidate" and report["accepted_by"] is None
    assert report["provenance"] == "offline"
    assert report["summary"]["paired"]["pass_rate_delta"] == 0
    row = report["rows"][0]
    assert row["answer"] == "诊区在二层。[E1]"
    assert row["answer_call"]["response"]["raw"]["id"] == "fake-answer"
    assert row["judge_input"]["reference"]["required_facts"] == sample_case()["required_facts"]
    assert row["judge_input"]["reference_documents"][0]["content"] == "诊区在二层。"
    assert row["judgment"]["support"][0]["quote"] == row["answer_input"]["evidence"][0]["content"]
    assert model.calls[0][0] == model.calls[2][0] == ANSWER_SYSTEM
    assert model.calls[0][1] == model.calls[2][1]
    assert "reference" not in model.calls[0][1] and "reference" in model.calls[1][1]


def test_full_denominator_includes_missing_retrieval_and_judge_failure(tmp_path):
    dataset, retrievals, corpus = inputs([sample_case("one"), sample_case("two")])
    del retrievals["arms"]["current"]["two"]
    retrievals["arms"]["improved"]["two"] = {"success": False, "results": [], "error": "retrieval down"}
    base = FakeModel()

    async def malformed(system, payload, stage):
        if stage == "judge": return {"text": "not JSON", "raw": {"text": "not JSON"}}
        return await base(system, payload, stage)

    report = run(tmp_path, dataset, retrievals, corpus, malformed)
    for arm in ARMS:
        score = report["summary"]["arms"][arm]
        assert score["total"] == 2 and score["passed"] == 0 and score["scored"] == 0
        assert score["mean_scores_full_denominator"]["correctness"] == 0
        assert score["mean_scores_scored_only"]["correctness"] is None
        assert len(score["failed_cases"]) == 2
    assert report["rows"][0]["answer"] == "诊区在二层。[E1]"
    assert report["rows"][0]["judge_call"]["response"]["text"] == "not JSON"
    assert report["complete"] is False


def test_partial_failures_cannot_improve_average_by_removing_failed_rows():
    cases = [sample_case("one"), sample_case("two")]
    rows = [{"case_id": "one", "arm": "current", "status": "scored", "passed": True, "judgment": good_judgment()}]
    result = aggregate(cases, rows)["arms"]["current"]
    assert result["pass_rate"] == .5
    assert result["mean_scores_full_denominator"]["correctness"] == .5
    assert result["mean_scores_scored_only"]["correctness"] == 1
    assert result["status_counts"] == {"scored": 1, "not_run": 1}


def test_answer_call_failure_is_preserved_and_judge_not_called(tmp_path):
    calls = []

    async def broken(system, payload, stage):
        calls.append(stage)
        raise TimeoutError("fake timeout")

    report = run(tmp_path, model=broken)
    assert calls == ["answer", "answer"]
    assert all(row["status"] == "answer_failed" for row in report["rows"])
    assert all(row["answer_call"]["error_type"] == "TimeoutError" for row in report["rows"])


def test_resume_reuses_completed_and_failed_requests_without_rebilling(tmp_path):
    model = FakeModel()
    first = run(tmp_path, model=model)
    second = run(tmp_path, model=model)
    assert len(model.calls) == 4
    assert first["summary"] == second["summary"]
    dataset, retrievals, corpus = inputs()
    retrievals["arms"]["improved"]["one"]["results"][0]["content"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        run(tmp_path, dataset, retrievals, corpus, model)
    assert not (tmp_path / "run.lock").exists()


def test_started_request_is_fsynced_before_call_and_uncertain_never_retried(tmp_path):
    path = tmp_path / "requests.jsonl"
    journal = Journal(path)
    calls = []

    async def interrupted(system, payload, stage):
        saved = json.loads(path.read_text().splitlines()[-1])
        assert saved["state"] == "started" and saved["input"] == {"question": "q"}
        calls.append(stage)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(journal.call("a", "system", {"question": "q"}, "answer", interrupted))
    result = asyncio.run(Journal(path).call("a", "system", {"question": "q"}, "answer", interrupted))
    assert result["state"] == "uncertain" and calls == ["answer"]


def test_failed_request_is_not_automatically_retried(tmp_path):
    journal = Journal(tmp_path / "requests.jsonl")
    count = 0

    async def broken(*args):
        nonlocal count
        count += 1
        raise RuntimeError("failed")

    first = asyncio.run(journal.call("key", "system", {}, "answer", broken))
    second = asyncio.run(journal.call("key", "system", {}, "answer", broken))
    assert first == second and count == 1


def test_truncated_generation_preserves_raw_and_is_not_judged(tmp_path):
    async def truncated(*args):
        return {"text": "部分回答", "raw": {"fragment": "partial"}, "stop_reason": "max_tokens"}
    report = run(tmp_path, model=truncated)
    assert all(row["status"] == "answer_failed" for row in report["rows"])
    assert report["rows"][0]["answer_call"]["response"]["raw"]["fragment"] == "partial"


def test_prepare_makes_no_calls_and_refuses_existing_or_concurrent_output(tmp_path):
    dataset, retrievals, corpus = inputs()
    model = FakeModel()
    report = asyncio.run(run_comparison(dataset, retrievals, corpus, output=tmp_path, invoke=model))
    assert model.calls == [] and report["complete"] is False
    assert report["summary"]["arms"]["current"]["total"] == 1
    (tmp_path / "run.lock").write_text("123")
    with pytest.raises(ValueError, match="run.lock"):
        run(tmp_path)


@pytest.mark.parametrize("mutation", ["unknown_case", "missing_success", "fallback", "missing_source", "empty_content"])
def test_retrieval_input_rejects_ambiguous_or_unsourced_results(mutation):
    dataset, retrievals, _ = inputs()
    row = retrievals["arms"]["current"]["one"]
    if mutation == "unknown_case": retrievals["arms"]["current"]["unknown"] = sample_retrieval()
    if mutation == "missing_success": del row["success"]
    if mutation == "fallback": row["fallback_used"] = True
    if mutation == "missing_source": row["results"] = [{"content": "无来源"}]
    if mutation == "empty_content": row["results"][0]["content"] = " "
    with pytest.raises(ValueError):
        validate_retrievals(retrievals, dataset["cases"])


def test_sdk_adapter_uses_identical_fixed_parameters_and_retains_raw_usage():
    calls = []
    response = SimpleNamespace(content=[{"type": "text", "text": "回答"}], id="response-id", stop_reason="end_turn",
                               usage=SimpleNamespace(model_dump=lambda **kwargs: {"input_tokens": 3}),
                               model_dump=lambda **kwargs: {"raw": "retained"})

    async def create(**kwargs):
        calls.append(kwargs)
        return response

    config = {"model": "fake-model", "answer_max_tokens": 1400, "judge_max_tokens": 3600,
              "request_options": {"extra_body": {"thinking": {"type": "disabled"}}}}
    invoke = sdk_invoker(SimpleNamespace(messages=SimpleNamespace(create=create)), config)
    result = asyncio.run(invoke("system", {"question": "q"}, "answer"))
    asyncio.run(invoke("judge", {"reference": {}}, "judge"))
    assert result["raw"] == {"raw": "retained"} and result["usage"] == {"input_tokens": 3}
    assert all(call["temperature"] == 0 and call["model"] == "fake-model" for call in calls)
    assert [call["max_tokens"] for call in calls] == [1400, 3600]
    assert all(call["extra_body"]["thinking"]["type"] == "disabled" for call in calls)
