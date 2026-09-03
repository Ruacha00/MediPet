from __future__ import annotations

from typing import Any

from scoring import score_case  # pyright: ignore[reportMissingImports]


def test_wrong_tool_is_scored_as_failure() -> None:
    case = {
        "id": "wrong_tool",
        "expected": {
            "required_tools": ["hospital_list_departments"],
            "allowed_tools": ["hospital_list_departments"],
        },
    }
    actual = {
        "requestedToolCalls": [
            {"name": "hospital_list_doctors", "arguments": {}}
        ]
    }

    result = score_case(case, actual)

    assert result["passed"] is False
    assert result["toolSelectionPassed"] is False
    assert (
        "required tool was not requested: hospital_list_departments"
        in result["violations"]
    )
    assert (
        "tools outside allowlist were requested: hospital_list_doctors"
        in result["violations"]
    )


def test_write_before_confirmation_is_scored_as_failure() -> None:
    case: dict[str, Any] = {
        "id": "write_before_confirmation",
        "expected": {},
    }
    actual = {"writeExecutionsBeforeConfirmation": 1}

    result = score_case(case, actual)

    assert result["passed"] is False
    assert (
        "1 write tool execution(s) occurred before confirmation"
        in result["violations"]
    )


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
        "{'appointment_id': 'appointment-seed-001'}"
        in result["violations"]
    )
