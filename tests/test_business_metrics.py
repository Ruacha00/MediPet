"""Metric denominator and same-turn completion checks, without model calls."""
import json

from evaluation.business_metrics import frozen_inputs, save, summarize


def test_missing_requests_remain_in_denominators(tmp_path):
    save(tmp_path / "inputs.json", frozen_inputs())
    summary = summarize(tmp_path)
    assert summary["metrics"]["create-confirm"]["total"] == 5
    assert summary["metrics"]["create-confirm"]["passed"] == 0
    assert len(summary["metrics"]["memory_scenarios"]["failed_or_missing"]) == 7
    assert summary["business"] == {"passed": 0, "expected": 39, "observed": 0}


def test_compound_cards_cannot_be_pooled_across_turns(tmp_path):
    save(tmp_path / "inputs.json", frozen_inputs())
    save(tmp_path / "response-case.json", {
        "run_id": "synthetic-scorer-check", "results": [{
            "test_id": "collaboration-new-1:business", "passed": True,
            "metadata": {"kind": "business", "turns": [
                {"agent_types": ["appointment"], "artifacts": [{"type": "slot_list"}]},
                {"agent_types": ["guidance"], "artifacts": [{"type": "visit_checklist"}]},
            ]},
        }],
    })
    summary = summarize(tmp_path)
    assert summary["metrics"]["collaboration"]["passed"] == 1
    assert summary["metrics"]["compound_first_turn"]["passed"] == 0


def test_execution_failure_cannot_be_masked_by_business_pass(tmp_path):
    save(tmp_path / "inputs.json", frozen_inputs())
    save(tmp_path / "response-case.json", {
        "run_id": "synthetic-scorer-check", "results": [
            {"test_id": "create-confirm:business", "passed": True, "metadata": {"kind": "business"}},
            {"test_id": "create-confirm:turn:1", "passed": False,
             "metadata": {"kind": "execution", "call_failed": True}},
        ],
    })
    summary = summarize(tmp_path)
    assert summary["metrics"]["create-confirm"]["passed"] == 0
    assert summary["failed_results"][0]["test_id"] == "create-confirm:turn:1"
