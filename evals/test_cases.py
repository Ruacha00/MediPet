from __future__ import annotations

from pathlib import Path

from run_eval import load_cases  # pyright: ignore[reportMissingImports]

CASES_PATH = Path(__file__).with_name("cases.jsonl")


def test_live_suite_has_24_core_cases_and_no_fault_injections() -> None:
    cases = load_cases(CASES_PATH)
    live_cases = [case for case in cases if case.get("live") is True]
    live_ids = {str(case["id"]) for case in live_cases}

    assert len(cases) == 28
    assert len(live_cases) == 24
    assert {
        "model_retry_once_before_output",
        "invalid_tool_args_safe_correction",
        "repeated_invalid_tool_loop",
        "model_timeout_safe_failure",
    }.isdisjoint(live_ids)
    assert {
        "create_tomorrow_general_date_bounded",
        "medical_boundary_no_indirect_triage",
    } <= live_ids


def test_symptom_only_cases_forbid_named_department_guidance() -> None:
    cases = {case["id"]: case for case in load_cases(CASES_PATH)}

    for case_id in (
        "catalog_symptom_no_auto_triage",
        "wayfinding_symptom_not_destination",
        "emergency_historical_no_trigger",
        "medical_boundary_no_diagnosis",
        "medical_boundary_no_indirect_triage",
    ):
        assertions = cases[case_id]["expected"].get("semantic_assertions", [])
        assert "no_named_department_guidance" in assertions
        assert "requires_manual_triage_guidance" in assertions
