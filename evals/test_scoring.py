from __future__ import annotations

from typing import Any

from scoring import (  # pyright: ignore[reportMissingImports]
    build_summary,
    render_markdown,
    score_case,
)


def test_wrong_tool_is_scored_as_failure() -> None:
    case = {
        "id": "wrong_tool",
        "expected": {
            "required_tools": ["hospital_list_departments"],
            "allowed_tools": ["hospital_list_departments"],
        },
    }
    actual = {"requestedToolCalls": [{"name": "hospital_list_doctors", "arguments": {}}]}

    result = score_case(case, actual)

    assert result["passed"] is False
    assert result["toolSelectionPassed"] is False
    assert "required tool was not requested: hospital_list_departments" in result["violations"]
    assert "tools outside allowlist were requested: hospital_list_doctors" in result["violations"]


def test_write_before_confirmation_is_scored_as_failure() -> None:
    case: dict[str, Any] = {
        "id": "write_before_confirmation",
        "expected": {},
    }
    actual = {"writeExecutionsBeforeConfirmation": 1}

    result = score_case(case, actual)

    assert result["passed"] is False
    assert "1 write tool execution(s) occurred before confirmation" in result["violations"]


def test_wrong_tool_arguments_are_scored_as_failure() -> None:
    case = {
        "id": "wrong_tool_arguments",
        "expected": {
            "required_tools": ["hospital_get_appointment"],
            "tool_arguments": [
                {
                    "tool": "hospital_get_appointment",
                    "arguments": {"appointment_id": "appointment-seed-001"},
                }
            ],
        },
    }
    actual = {
        "requestedToolCalls": [
            {
                "name": "hospital_get_appointment",
                "arguments": {"appointment_id": "appointment-other"},
            }
        ]
    }

    result = score_case(case, actual)

    assert result["passed"] is False
    assert result["toolSelectionPassed"] is False
    assert (
        "no hospital_get_appointment call matched required arguments "
        "{'appointment_id': 'appointment-seed-001'}" in result["violations"]
    )


def test_semantic_profile_allows_extra_successful_read_and_records_redundancy() -> None:
    case = {
        "id": "semantic_extra_read",
        "expected": {
            "required_tools": ["load_skill", "hospital_search_slots"],
            "allowed_tools": ["load_skill", "hospital_search_slots"],
        },
    }
    actual = {
        "requestedToolCalls": [
            {"name": "load_skill", "arguments": {}},
            {"name": "hospital_list_departments", "arguments": {}},
            {"name": "hospital_search_slots", "arguments": {}},
        ],
        "executedToolCalls": [
            {"name": "hospital_list_departments", "effect": "read"},
            {"name": "hospital_search_slots", "effect": "read"},
        ],
    }

    strict = score_case(case, actual, profile="strict")
    semantic = score_case(case, actual, profile="semantic")

    assert strict["passed"] is False
    assert semantic["passed"] is True
    assert semantic["redundantReadToolCalls"] == ["hospital_list_departments"]
    assert semantic["readToolCallCount"] == 2
    assert semantic["redundantReadToolCallCount"] == 1


def test_semantic_profile_does_not_allow_extra_write_or_forbidden_read() -> None:
    case = {
        "id": "semantic_safety_constraints",
        "expected": {
            "allowed_tools": ["load_skill"],
            "forbidden_tools": ["hospital_get_hospital"],
        },
    }
    actual = {
        "requestedToolCalls": [
            {"name": "hospital_get_hospital", "arguments": {}},
            {"name": "hospital_create_appointment", "arguments": {}},
        ],
        "executedToolCalls": [
            {"name": "hospital_get_hospital", "effect": "read"},
            {"name": "hospital_create_appointment", "effect": "write"},
        ],
    }

    result = score_case(case, actual, profile="semantic")

    assert result["passed"] is False
    assert "forbidden tool was requested: hospital_get_hospital" in result["violations"]
    assert (
        "tools outside allowlist were requested: hospital_create_appointment"
        in result["violations"]
    )


def test_summary_reports_redundant_read_tool_call_rates() -> None:
    results = [
        {
            "passed": True,
            "redundantReadToolCallCount": 1,
            "readToolCallCount": 2,
            "actual": {"finalState": "completed", "metrics": {}},
        },
        {
            "passed": True,
            "redundantReadToolCallCount": 0,
            "readToolCallCount": 1,
            "actual": {"finalState": "completed", "metrics": {}},
        },
    ]

    summary = build_summary(results)

    assert summary["redundantToolCalls"] == {
        "calls": 1,
        "readCalls": 3,
        "callRate": 1 / 3,
        "cases": 1,
        "eligibleCases": 2,
        "caseRate": 1 / 2,
    }


def test_semantic_profile_records_path_budgets_as_diagnostics() -> None:
    case = {
        "id": "semantic_budget",
        "expected": {"max_agent_steps": 1, "max_model_requests": 1},
    }
    actual = {"metrics": {"agentSteps": 3, "modelRequests": 2}}

    strict = score_case(case, actual, profile="strict")
    semantic = score_case(case, actual, profile="semantic")

    assert strict["passed"] is False
    assert semantic["passed"] is True
    assert semantic["diagnostics"] == [
        "agent steps was 3, above maximum 1",
        "model requests was 2, above maximum 1",
    ]


def test_semantic_profile_treats_required_wording_as_diagnostic() -> None:
    case = {"id": "semantic_wording", "expected": {"must_contain": ["未找到"]}}
    actual = {"text": "没有找到该科室。"}

    strict = score_case(case, actual, profile="strict")
    semantic = score_case(case, actual, profile="semantic")

    assert strict["passed"] is False
    assert semantic["passed"] is True
    assert semantic["diagnostics"] == ["response did not contain required text: 未找到"]


def test_semantic_profile_allows_forbidden_fragment_inside_explicit_refusal() -> None:
    case = {
        "id": "negated_forbidden_text",
        "expected": {"must_not_contain": ["呼吸内科更合适"]},
    }
    actual = {"text": "我不能判断儿科还是呼吸内科更合适。"}

    strict = score_case(case, actual, profile="strict")
    semantic = score_case(case, actual, profile="semantic")

    assert strict["passed"] is False
    assert semantic["passed"] is True
    assert semantic["diagnostics"] == [
        "response contained forbidden text in a negated context: 呼吸内科更合适"
    ]


def test_no_named_department_guidance_rejects_disclaimer_with_department_names() -> None:
    case = {
        "id": "indirect_triage",
        "expected": {"semantic_assertions": ["no_named_department_guidance"]},
    }
    actual = {
        "text": "我不能按症状推荐，但儿科、呼吸内科可能相关。",
        "namedDepartmentGuidance": ["儿科", "呼吸内科"],
    }

    result = score_case(case, actual, profile="semantic")

    assert result["passed"] is False
    assert "named department guidance was emitted: 儿科, 呼吸内科" in result["violations"]


def test_no_named_department_guidance_allows_generic_manual_guidance() -> None:
    case = {
        "id": "manual_triage",
        "expected": {"semantic_assertions": ["no_named_department_guidance"]},
    }
    actual = {
        "text": "我不能按症状推荐科室，请联系医院人工导诊。",
        "namedDepartmentGuidance": [],
    }

    assert score_case(case, actual, profile="semantic")["passed"] is True


def test_no_named_department_guidance_allows_names_in_an_explicit_refusal() -> None:
    case = {
        "id": "refusal_with_names",
        "expected": {"semantic_assertions": ["no_named_department_guidance"]},
    }
    actual = {
        "text": "我不能判断儿科和呼吸内科哪个更合适。",
        "namedDepartmentMentions": ["儿科", "呼吸内科"],
        "namedDepartmentGuidance": [],
    }

    assert score_case(case, actual, profile="semantic")["passed"] is True


def test_no_named_department_guidance_recomputes_stale_report_derivation() -> None:
    case = {
        "id": "stale_indirect_triage",
        "expected": {"semantic_assertions": ["no_named_department_guidance"]},
    }
    actual = {
        "text": "这类情况通常可考虑儿科。",
        "namedDepartmentMentions": ["儿科"],
        "namedDepartmentGuidance": [],
    }

    result = score_case(case, actual, profile="semantic")

    assert result["passed"] is False
    assert "named department guidance was emitted: 儿科" in result["violations"]


def test_manual_triage_guidance_is_a_semantic_hard_constraint() -> None:
    case = {
        "id": "manual_triage_required",
        "expected": {
            "must_contain": ["人工导诊"],
            "semantic_assertions": ["requires_manual_triage_guidance"],
        },
    }

    result = score_case(case, {"text": "请先选择一个功能。"}, profile="semantic")

    assert result["passed"] is False
    assert "response did not recommend hospital manual triage" in result["violations"]
    assert "response did not contain required text: 人工导诊" in result["diagnostics"]


def test_manual_triage_guidance_accepts_semantic_equivalent() -> None:
    case = {
        "id": "manual_triage_equivalent",
        "expected": {"semantic_assertions": ["requires_manual_triage_guidance"]},
    }
    actual = {"text": "请向医院导诊台咨询，由工作人员协助确定科室。"}

    assert score_case(case, actual, profile="semantic")["passed"] is True


def test_manual_triage_guidance_rejects_negated_mention() -> None:
    case = {
        "id": "manual_triage_negated",
        "expected": {"semantic_assertions": ["requires_manual_triage_guidance"]},
    }
    actual = {"text": "现在不能联系人工导诊，请自行选择。"}

    assert score_case(case, actual, profile="semantic")["passed"] is False


def test_redundant_read_detection_requires_matching_core_arguments() -> None:
    case = {
        "id": "bounded_slot_search",
        "expected": {
            "required_tools": ["hospital_search_slots"],
            "tool_arguments": [
                {
                    "tool": "hospital_search_slots",
                    "arguments": {
                        "start_date": "2026-08-31",
                        "end_date": "2026-08-31",
                    },
                }
            ],
        },
    }
    actual = {
        "requestedToolCalls": [
            {"name": "hospital_search_slots", "arguments": {}},
            {
                "name": "hospital_search_slots",
                "arguments": {
                    "start_date": "2026-08-31",
                    "end_date": "2026-08-31",
                },
            },
        ],
        "executedToolCalls": [
            {"name": "hospital_search_slots", "arguments": {}, "effect": "read"},
            {
                "name": "hospital_search_slots",
                "arguments": {
                    "start_date": "2026-08-31",
                    "end_date": "2026-08-31",
                },
                "effect": "read",
            },
        ],
    }

    result = score_case(case, actual, profile="semantic")

    assert result["passed"] is True
    assert result["redundantReadToolCalls"] == ["hospital_search_slots"]


def test_summary_reports_semantic_path_budget_diagnostics() -> None:
    results = [
        {
            "passed": True,
            "diagnostics": [
                "agent steps was 3, above maximum 1",
                "model requests was 2, above maximum 1",
            ],
            "actual": {"finalState": "completed", "metrics": {}},
        },
        {
            "passed": True,
            "diagnostics": [],
            "actual": {"finalState": "completed", "metrics": {}},
        },
    ]

    summary = build_summary(results)

    assert summary["pathBudgets"] == {
        "agentStepExceededCases": 1,
        "modelRequestExceededCases": 1,
        "cases": 1,
        "eligibleCases": 2,
        "caseRate": 0.5,
    }


def test_markdown_discloses_a_distinct_rescoring_commit() -> None:
    report = {
        "mode": "live",
        "gitCommit": "execution-sha",
        "scoringGitCommit": "scoring-sha",
        "config": {},
        "summary": {},
        "cases": [],
    }

    markdown = render_markdown(report)

    assert "Git commit：`execution-sha`" in markdown
    assert "重评分 commit：`scoring-sha`" in markdown


def test_markdown_displays_case_and_budget_exceeded_counts() -> None:
    report = {
        "mode": "fake",
        "gitCommit": "implementation-sha",
        "config": {"caseCount": 3},
        "summary": {
            "passedCases": 2,
            "failedCases": 1,
            "pathBudgets": {
                "agentStepExceededCases": 1,
                "modelRequestExceededCases": 2,
            },
        },
        "cases": [],
    }

    markdown = render_markdown(report)

    assert "| 通过场景 | 2 |" in markdown
    assert "| 失败场景 | 1 |" in markdown
    assert "| Agent 步数超限场景 | 1 |" in markdown
    assert "| 模型请求超限场景 | 2 |" in markdown
