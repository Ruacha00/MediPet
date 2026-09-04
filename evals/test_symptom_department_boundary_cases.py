from __future__ import annotations

from pathlib import Path
from typing import Any

from run_eval import load_cases  # pyright: ignore[reportMissingImports]

EVALS_DIR = Path(__file__).resolve().parent
POLICY_CASES_PATH = EVALS_DIR / "symptom-department-boundary-cases.jsonl"
CORE_CASES_PATH = EVALS_DIR / "cases.jsonl"
REQUIRED_ROUTES = {
    "manual_triage",
    "explicit_target_agent",
    "ordinary_agent",
    "emergency_handoff",
}
DETERMINISTIC_ROUTES = {"manual_triage", "emergency_handoff"}


def _policy_cases() -> list[dict[str, Any]]:
    return load_cases(POLICY_CASES_PATH)


def test_policy_case_ids_are_unique_and_all_cases_are_non_live() -> None:
    cases = _policy_cases()
    case_ids = [case["id"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    assert cases
    assert all(case.get("live") is False for case in cases)


def test_policy_cases_represent_every_boundary_route() -> None:
    routes = {case.get("route") for case in _policy_cases()}

    assert routes == REQUIRED_ROUTES


def test_deterministic_routes_have_zero_model_and_tool_budgets() -> None:
    deterministic = [
        case for case in _policy_cases() if case.get("route") in DETERMINISTIC_ROUTES
    ]

    assert deterministic
    for case in deterministic:
        expected = case["expected"]
        assert case["fake_script"] == []
        assert expected["allowed_skills"] == []
        assert expected["allowed_tools"] == []
        assert expected["max_agent_steps"] == 0
        assert expected["max_model_requests"] == 0


def test_fake_scripts_only_use_each_cases_declared_skills_and_tools() -> None:
    for case in _policy_cases():
        expected = case["expected"]
        allowed_tools = set(expected["allowed_tools"])
        required_tools = set(expected.get("required_tools", []))
        allowed_skills = set(expected["allowed_skills"])
        scripted_tools = {
            action["tool"] for action in case["fake_script"] if "tool" in action
        }
        scripted_skills = {
            action["arguments"]["slug"]
            for action in case["fake_script"]
            if action.get("tool") == "load_skill"
        }

        assert required_tools <= allowed_tools, case["id"]
        assert scripted_tools <= allowed_tools, case["id"]
        assert scripted_skills <= allowed_skills, case["id"]


def test_independent_policy_corpus_does_not_change_live_core_case_count() -> None:
    live_core_cases = [case for case in load_cases(CORE_CASES_PATH) if case.get("live") is True]

    assert len(live_core_cases) == 24
