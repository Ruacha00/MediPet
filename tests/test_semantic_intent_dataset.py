"""U002 data contracts only: no model import, network, storage, or inference."""

import difflib
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evaluation/cases/semantic_intent"


def read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum())


def bigrams(text):
    value = normalized(text)
    return {value[index:index + 2] for index in range(len(value) - 1)} or {value}


def jaccard(left, right):
    return len(left & right) / len(left | right) if left | right else 1.0


def all_cases():
    return read("dev.json")["cases"] + read("holdout.json")["cases"]


@pytest.mark.parametrize("split,minimum", [("dev", 6), ("holdout", 10)])
def test_schema_and_original_label_coverage(split, minimum):
    document = read(f"{split}.json")
    assert document["schema_version"] == 1 and document["split"] == split
    expected_labels = {case["expected_intent"] for case in json.loads(
        (ROOT / "evaluation/cases/intents.json").read_text(encoding="utf-8"))["cases"]}
    counts = Counter(case["expected_intent"] for case in document["cases"]
                     if case["evaluation_group"] not in {"context_required", "ambiguous"})
    assert set(counts) == expected_labels
    assert all(count >= minimum for count in counts.values())
    required = {"id", "message", "expected_intent", "expression_family", "phenomena", "label_reason",
                "requires_context", "history", "evaluation_group", "vector_primary", "llm_failure_eligible",
                "business_intent", "lexical_overlap"}
    for case in document["cases"]:
        assert required == case.keys()
        assert case["expected_intent"] in expected_labels
        assert all(isinstance(case[key], str) and case[key].strip()
                   for key in ("id", "message", "expression_family", "label_reason"))
        assert case["id"].startswith(f"{split}:")
        assert "synthetic" in case["phenomena"]
        assert case["requires_context"] is bool(case["history"])
        for message in case["history"]:
            assert message.keys() == {"role", "content"}
            assert message["role"] in {"user", "assistant"} and message["content"].strip()


def test_ids_texts_and_expression_families_do_not_cross_splits():
    cases = all_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({normalized(case["message"]) for case in cases}) == len(cases)
    dev = {case["expression_family"] for case in read("dev.json")["cases"]}
    holdout = {case["expression_family"] for case in read("holdout.json")["cases"]}
    assert dev.isdisjoint(holdout)
    reference_texts = {normalized(case["message"]) for case in read("references.json")["cases"]}
    assert not reference_texts.intersection(normalized(case["message"]) for case in cases)


def test_authored_family_pairs_stay_together_in_declared_splits():
    actual = {case["message"]: (case["expression_family"], case["id"].split(":")[0])
              for case in all_cases()}
    for label, families in read("authoring-families.json")["labels"].items():
        assert len(families) == 8
        for index, (family, messages) in enumerate(families):
            assert len(messages) == 2
            expected = (f"{label}:{family}", "dev" if index < 3 else "holdout")
            assert all(actual[message] == expected for message in messages)


def test_boundary_groups_cannot_inflate_vector_or_failure_coverage_metrics():
    groups = Counter()
    for case in all_cases():
        group = case["evaluation_group"]
        groups[group] += 1
        assert group in {"single_turn", "emergency", "other", "context_required", "ambiguous"}
        eligible = group == "single_turn"
        assert case["vector_primary"] is eligible
        assert case["llm_failure_eligible"] is eligible
        assert case["requires_context"] is (group == "context_required")
        if group in {"other", "ambiguous"}:
            assert case["expected_intent"] == "other"
        if group == "emergency":
            assert case["expected_intent"] == "emergency"
        if not eligible or not case["business_intent"]:
            assert not case["lexical_overlap"]["low_literal_overlap"]
    assert groups == {"single_turn": 256, "emergency": 16, "other": 16,
                      "context_required": 8, "ambiguous": 4}


def test_low_overlap_is_computed_from_frozen_characters_not_model_output():
    references = read("references.json")["cases"]
    reference_grams = [bigrams(case["message"]) for case in references]
    for case in all_cases():
        scores = [jaccard(bigrams(case["message"]), value) for value in reference_grams]
        index = max(range(len(scores)), key=scores.__getitem__)
        metadata = case["lexical_overlap"]
        assert metadata["rule"] == "nfkc_alnum_bigram_jaccard_v1"
        assert metadata["max_reference_jaccard"] == pytest.approx(scores[index], abs=1e-8)
        assert metadata["nearest_reference_id"] == references[index]["id"]
        expected = case["vector_primary"] and case["business_intent"] and scores[index] <= 0.20
        assert metadata["low_literal_overlap"] is expected
        assert ("low_literal_overlap" in case["phenomena"]) is expected
    holdout = [case for case in read("holdout.json")["cases"]
               if case["lexical_overlap"]["low_literal_overlap"]]
    assert len(holdout) >= 40
    assert len({case["expected_intent"] for case in holdout}) >= 8


def test_near_duplicate_audit_is_complete_and_exceptions_are_outside_primary_metrics():
    cases, references = all_cases(), read("references.json")["cases"]
    actual = set()
    for index, left in enumerate(cases):
        for right in references + cases[index + 1:]:
            if left.get("expression_family") == right.get("expression_family"):
                continue
            score = jaccard(bigrams(left["message"]), bigrams(right["message"]))
            ratio = difflib.SequenceMatcher(None, normalized(left["message"]),
                                           normalized(right["message"]), autojunk=False).ratio()
            if score >= 0.60 or ratio >= 0.84:
                actual.add((left["id"], right["id"]))
    audit = read("overlap-review.json")
    declared = {(item["left"], item["right"]) for item in audit["flags"]}
    assert actual == declared
    by_id = {case["id"]: case for case in cases}
    for item in audit["flags"]:
        assert item["decision"] == "retain_boundary_only" and item["reason"].strip()
        assert not by_id[item["left"]]["vector_primary"]
        assert not by_id[item["left"]]["llm_failure_eligible"]


def test_frozen_file_hashes_and_original_regression_files_are_unchanged():
    manifest = read("manifest.json")
    assert manifest["inference_used_for_authoring"] is False
    assert read("references.json")["learn_samples"] == []
    for name, expected in manifest["file_sha256"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == expected
    for name, expected in manifest["original_regression_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    assert manifest["review"]["status"] in {"pending", "approved"}
    if manifest["review"]["status"] == "approved":
        assert manifest["review"]["reviewer"] != manifest["author"]
        assert manifest["review"]["evidence"]
