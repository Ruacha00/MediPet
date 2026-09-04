import pytest

from medipet.triage import (
    MANUAL_TRIAGE_GUIDANCE,
    DepartmentBoundaryReason,
    DepartmentBoundaryResult,
    evaluate_department_boundary,
)


@pytest.mark.parametrize(
    "message",
    [
        "孩子发烧了，应该挂哪科？",
        "肚子疼要看什么科？",
        "头痛应该去哪里？",
        "咳嗽看儿科还是全科医学科？",
        "发烧，儿科合适吗？",
    ],
)
def test_current_symptom_routing_requests_require_manual_triage(message: str) -> None:
    decision = evaluate_department_boundary(current_message=message)

    assert decision.result is DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED
    assert decision.reason is DepartmentBoundaryReason.CURRENT_SYMPTOM_ROUTING_REQUEST
    assert decision.response == MANUAL_TRIAGE_GUIDANCE
    assert decision.allows_agent is False


@pytest.mark.parametrize(
    ("message", "has_selected_slot", "reason"),
    [
        (
            "发烧，但我已经决定挂儿科，帮我查号源。",
            False,
            DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        ),
        (
            "儿科有哪些医生？",
            False,
            DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        ),
        (
            "我在主入口，想去门诊检验处。",
            False,
            DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        ),
        (
            "我选择这个号源。",
            True,
            DepartmentBoundaryReason.CURRENT_SLOT_SELECTION,
        ),
    ],
)
def test_current_explicit_targets_continue_to_the_agent(
    message: str,
    has_selected_slot: bool,
    reason: DepartmentBoundaryReason,
) -> None:
    decision = evaluate_department_boundary(
        current_message=message,
        has_selected_slot=has_selected_slot,
    )

    assert decision.result is DepartmentBoundaryResult.EXPLICIT_TARGET_ALLOWED
    assert decision.reason is reason
    assert decision.response is None
    assert decision.allows_agent is True


@pytest.mark.parametrize(
    "message",
    [
        "帮我整理一下：孩子发烧两天，夜里更明显。",
        "这家医院有哪些科室？",
        "目前没有头痛，只想了解一般门诊流程。",
        "孩子昨天呼吸困难，今天已经好了，想了解复诊流程。",
        "什么是发热？请做科普说明。",
        "我没有头痛，不是在问挂哪科，只想整理病史。",
        "孩子昨天头痛已经好了，当时应该挂哪科？我只是在整理病史。",
        "如果有人发烧，应该挂哪科？请做科普说明。",
    ],
)
def test_ordinary_assistance_does_not_inherit_a_routing_decision(message: str) -> None:
    decision = evaluate_department_boundary(current_message=message)

    assert decision.result is DepartmentBoundaryResult.AGENT_ALLOWED
    assert decision.reason is DepartmentBoundaryReason.ORDINARY_ASSISTANCE
    assert decision.response is None
    assert decision.allows_agent is True


@pytest.mark.parametrize(
    ("current_message", "expected_result", "expected_reason"),
    [
        (
            "那要挂哪科？",
            DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED,
            DepartmentBoundaryReason.FOLLOWUP_SYMPTOM_ROUTING_REQUEST,
        ),
        (
            "儿科是否合适？",
            DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED,
            DepartmentBoundaryReason.FOLLOWUP_SYMPTOM_ROUTING_REQUEST,
        ),
        (
            "儿科有哪些医生？",
            DepartmentBoundaryResult.EXPLICIT_TARGET_ALLOWED,
            DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        ),
        (
            "这家医院有哪些科室？",
            DepartmentBoundaryResult.AGENT_ALLOWED,
            DepartmentBoundaryReason.ORDINARY_ASSISTANCE,
        ),
        (
            "我想去门诊检验处。",
            DepartmentBoundaryResult.EXPLICIT_TARGET_ALLOWED,
            DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        ),
    ],
)
def test_only_routing_followups_use_the_previous_participant_symptom_context(
    current_message: str,
    expected_result: DepartmentBoundaryResult,
    expected_reason: DepartmentBoundaryReason,
) -> None:
    decision = evaluate_department_boundary(
        current_message=current_message,
        previous_participant_message="孩子发烧两天了。",
    )

    assert decision.result is expected_result
    assert decision.reason is expected_reason
    assert decision.response == (
        MANUAL_TRIAGE_GUIDANCE
        if expected_result is DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED
        else None
    )


def test_followup_without_previous_symptoms_stays_in_ordinary_assistance() -> None:
    decision = evaluate_department_boundary(
        current_message="那要挂哪科？",
        previous_participant_message="请介绍一般门诊流程。",
    )

    assert decision.result is DepartmentBoundaryResult.AGENT_ALLOWED
    assert decision.reason is DepartmentBoundaryReason.ORDINARY_ASSISTANCE
