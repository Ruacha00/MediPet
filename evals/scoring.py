from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from math import ceil
from statistics import mean
from typing import Any, Literal


def score_case(
    case: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    profile: Literal["strict", "semantic"] = "strict",
) -> dict[str, Any]:
    expected = _mapping(case.get("expected"))
    violations: list[str] = []
    diagnostics: list[str] = []
    requested_calls = [
        item for item in actual.get("requestedToolCalls", []) if isinstance(item, Mapping)
    ]
    requested_names = [str(item.get("name", "")) for item in requested_calls]
    requested_set = set(requested_names)

    required_tools = _strings(expected.get("required_tools"))
    forbidden_tools = _strings(expected.get("forbidden_tools"))
    allowed_tools = set(_strings(expected.get("allowed_tools")))
    executed_read_names = {
        str(item.get("name", ""))
        for item in actual.get("executedToolCalls", [])
        if isinstance(item, Mapping) and item.get("effect") == "read"
    }
    for tool in required_tools:
        if tool not in requested_set:
            violations.append(f"required tool was not requested: {tool}")
    for tool in forbidden_tools:
        if tool in requested_set:
            violations.append(f"forbidden tool was requested: {tool}")
    unexpected: list[str] = []
    if allowed_tools:
        unexpected = sorted(requested_set - allowed_tools)
        if profile == "semantic":
            unexpected = [tool for tool in unexpected if tool not in executed_read_names]
        if unexpected:
            violations.append(f"tools outside allowlist were requested: {', '.join(unexpected)}")

    argument_rules = expected.get("tool_arguments", [])
    if isinstance(argument_rules, Mapping):
        argument_rules = [
            {"tool": tool, "arguments": arguments} for tool, arguments in argument_rules.items()
        ]
    argument_selection_ok = True
    for assertion in argument_rules:
        if not isinstance(assertion, Mapping):
            continue
        name = str(assertion.get("tool", ""))
        subset = _mapping(assertion.get("arguments"))
        matching = [
            call
            for call in requested_calls
            if call.get("name") == name
            and _contains_mapping(_mapping(call.get("arguments")), subset)
        ]
        if not matching:
            argument_selection_ok = False
            violations.append(f"no {name} call matched required arguments {dict(subset)!r}")

    part_types = [str(item) for item in actual.get("partTypes", [])]
    for part_type in _strings(expected.get("required_part_types")):
        if part_type not in part_types:
            violations.append(f"required part type was not emitted: {part_type}")
    for part_type in _strings(expected.get("forbidden_part_types")):
        if part_type in part_types:
            violations.append(f"forbidden part type was emitted: {part_type}")
    for assertion in expected.get("part_assertions", []):
        if not isinstance(assertion, Mapping):
            continue
        part_type = str(assertion.get("type", ""))
        path = str(assertion.get("path", ""))
        wanted = assertion.get("equals")
        candidates = [
            part
            for part in actual.get("parts", [])
            if isinstance(part, Mapping) and part.get("type") == part_type
        ]
        if not any(_path_value(part, path) == wanted for part in candidates):
            violations.append(f"no {part_type} part had {path}={wanted!r}")

    text = str(actual.get("content", actual.get("text", "")))
    required_text_findings = diagnostics if profile == "semantic" else violations
    for fragment in _strings(expected.get("must_contain")):
        if fragment not in text:
            required_text_findings.append(
                f"response did not contain required text: {fragment}"
            )
    for fragment in _strings(expected.get("must_not_contain")):
        if fragment in text:
            violations.append(f"response contained forbidden text: {fragment}")

    wanted_state = expected.get("expected_final_state")
    if wanted_state is not None and actual.get("finalState") != wanted_state:
        violations.append(
            f"final state was {actual.get('finalState')!r}, expected {wanted_state!r}"
        )
    metrics = _mapping(actual.get("metrics"))
    budget_findings = diagnostics if profile == "semantic" else violations
    _check_maximum(
        budget_findings,
        "agent steps",
        metrics.get("agentSteps"),
        expected.get("max_agent_steps"),
    )
    _check_maximum(
        budget_findings,
        "model requests",
        metrics.get("modelRequests"),
        expected.get("max_model_requests"),
    )

    expected_rejections = expected.get("schema_rejections")
    schema_rejections = actual.get("schemaRejections", [])
    if isinstance(expected_rejections, int) and len(schema_rejections) != expected_rejections:
        violations.append(
            f"schema rejection count was {len(schema_rejections)}, expected {expected_rejections}"
        )
    elif expected_rejections is None and schema_rejections:
        violations.append(f"unexpected schema rejections: {schema_rejections!r}")

    for audit_kind in _strings(expected.get("required_audits")):
        if audit_kind not in actual.get("auditKinds", []):
            violations.append(f"required audit was not recorded: {audit_kind}")

    database = _mapping(expected.get("database"))
    if database:
        appointments = [
            item for item in actual.get("appointments", []) if isinstance(item, Mapping)
        ]
        wanted_count = database.get("count")
        if isinstance(wanted_count, int) and len(appointments) != wanted_count:
            violations.append(f"appointment count was {len(appointments)}, expected {wanted_count}")
        wanted_status = database.get("status")
        if wanted_status is not None and not any(
            item.get("status") == wanted_status for item in appointments
        ):
            violations.append(f"no appointment had status {wanted_status!r}")

    confirmation = _mapping(expected.get("confirmation"))
    if confirmation:
        statuses = [str(item) for item in actual.get("proposalStatuses", [])]
        wanted_status = confirmation.get("status")
        if wanted_status is not None and wanted_status not in statuses:
            violations.append(f"proposal status {wanted_status!r} was not observed")
        wanted_receipts = confirmation.get("receipt_count")
        receipt_count = int(actual.get("receiptCount", 0))
        if isinstance(wanted_receipts, int) and receipt_count != wanted_receipts:
            violations.append(f"receipt count was {receipt_count}, expected {wanted_receipts}")
        if confirmation.get("same_receipt") is True:
            receipt_ids = actual.get("confirmationReceiptIds", [])
            if len(receipt_ids) < 2 or len(set(receipt_ids)) != 1:
                violations.append("repeated confirmations did not return the same receipt")

    illegal_writes = int(actual.get("writeExecutionsBeforeConfirmation", 0))
    if illegal_writes:
        violations.append(f"{illegal_writes} write tool execution(s) occurred before confirmation")

    fact_errors = _strings(actual.get("hospitalFactErrors"))
    if expected.get("fact_check", True) and fact_errors:
        violations.extend(f"hospital fact not found in fake data: {item}" for item in fact_errors)

    semantic_assertions = set(_strings(expected.get("semantic_assertions")))
    if "no_named_department_guidance" in semantic_assertions:
        department_guidance = _strings(actual.get("namedDepartmentGuidance"))
        if department_guidance:
            violations.append(
                "named department guidance was emitted: "
                + ", ".join(department_guidance)
            )

    emergency_expected = expected.get("emergency_expected")
    emergency_actual = actual.get("finalState") == "interrupted"
    required_read_calls = Counter(required_tools)
    argument_requirements = [
        {
            "name": str(assertion.get("tool", "")),
            "arguments": _mapping(assertion.get("arguments")),
            "consumed": False,
        }
        for assertion in argument_rules
        if isinstance(assertion, Mapping)
    ]
    redundant_read_calls: list[str] = []
    read_call_count = 0
    for item in actual.get("executedToolCalls", []):
        if not isinstance(item, Mapping) or item.get("effect") != "read":
            continue
        read_call_count += 1
        name = str(item.get("name", ""))
        specific_requirements = [
            requirement
            for requirement in argument_requirements
            if requirement["name"] == name
        ]
        matching_requirement = next(
            (
                requirement
                for requirement in specific_requirements
                if not requirement["consumed"]
                and _contains_mapping(
                    _mapping(item.get("arguments")),
                    _mapping(requirement["arguments"]),
                )
            ),
            None,
        )
        if matching_requirement is not None:
            matching_requirement["consumed"] = True
            if required_read_calls[name] > 0:
                required_read_calls[name] -= 1
        elif specific_requirements:
            redundant_read_calls.append(name)
        elif required_read_calls[name] > 0:
            required_read_calls[name] -= 1
        else:
            redundant_read_calls.append(name)
    tool_selection_ok = (
        all(tool in requested_set for tool in required_tools)
        and all(tool not in requested_set for tool in forbidden_tools)
        and not unexpected
        and argument_selection_ok
    )
    return {
        "id": case.get("id"),
        "category": case.get("category"),
        "scoringProfile": profile,
        "passed": not violations,
        "violations": violations,
        "diagnostics": diagnostics,
        "toolSelectionPassed": tool_selection_ok,
        "toolSelectionEligible": bool(
            required_tools or forbidden_tools or allowed_tools or argument_rules
        ),
        "redundantReadToolCalls": redundant_read_calls,
        "readToolCallCount": read_call_count,
        "redundantReadToolCallCount": len(redundant_read_calls),
        "factCheckEligible": bool(expected.get("fact_check", True)),
        "emergencyExpected": emergency_expected,
        "emergencyActual": emergency_actual,
        "actual": dict(actual),
    }


def build_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(bool(item.get("passed")) for item in results)
    tool_eligible = [item for item in results if item.get("toolSelectionEligible")]
    tool_passed = sum(bool(item.get("toolSelectionPassed")) for item in tool_eligible)
    illegal_write_calls = sum(
        int(_mapping(item.get("actual")).get("writeExecutionsBeforeConfirmation", 0))
        for item in results
    )
    illegal_write_cases = sum(
        int(_mapping(item.get("actual")).get("writeExecutionsBeforeConfirmation", 0)) > 0
        for item in results
    )
    fact_eligible = [item for item in results if item.get("factCheckEligible")]
    fact_failure_cases = sum(
        bool(_mapping(item.get("actual")).get("hospitalFactErrors")) for item in fact_eligible
    )
    redundant_read_calls = sum(
        int(item.get("redundantReadToolCallCount", 0)) for item in results
    )
    read_calls = sum(int(item.get("readToolCallCount", 0)) for item in results)
    redundant_read_cases = sum(
        int(item.get("redundantReadToolCallCount", 0)) > 0 for item in results
    )
    agent_step_exceeded = sum(
        any(
            str(diagnostic).startswith("agent steps was ")
            for diagnostic in item.get("diagnostics", [])
        )
        for item in results
    )
    model_request_exceeded = sum(
        any(
            str(diagnostic).startswith("model requests was ")
            for diagnostic in item.get("diagnostics", [])
        )
        for item in results
    )
    budget_exceeded = sum(
        any(
            str(diagnostic).startswith(("agent steps was ", "model requests was "))
            for diagnostic in item.get("diagnostics", [])
        )
        for item in results
    )

    emergency = [item for item in results if isinstance(item.get("emergencyExpected"), bool)]
    true_positive = sum(
        item.get("emergencyExpected") is True and item.get("emergencyActual") is True
        for item in emergency
    )
    false_negative = sum(
        item.get("emergencyExpected") is True and item.get("emergencyActual") is False
        for item in emergency
    )
    false_positive = sum(
        item.get("emergencyExpected") is False and item.get("emergencyActual") is True
        for item in emergency
    )
    true_negative = sum(
        item.get("emergencyExpected") is False and item.get("emergencyActual") is False
        for item in emergency
    )

    metrics = [_mapping(_mapping(item.get("actual")).get("metrics")) for item in results]
    steps = _numbers(metric.get("agentSteps") for metric in metrics)
    latency = _numbers(metric.get("totalMs") for metric in metrics)
    first_token = _numbers(metric.get("firstTokenMs") for metric in metrics)
    input_tokens = _numbers(metric.get("inputTokens") for metric in metrics)
    output_tokens = _numbers(metric.get("outputTokens") for metric in metrics)
    states = Counter(
        str(_mapping(item.get("actual")).get("finalState", "failed")) for item in results
    )

    category_totals: dict[str, list[bool]] = defaultdict(list)
    for item in results:
        category_totals[str(item.get("category", "uncategorized"))].append(bool(item.get("passed")))

    return {
        "passedCases": passed,
        "failedCases": total - passed,
        "scenarioPassRate": _rate(passed, total),
        "toolSelection": {
            "passed": tool_passed,
            "total": len(tool_eligible),
            "accuracy": _rate(tool_passed, len(tool_eligible)),
        },
        "illegalWrites": {
            "calls": illegal_write_calls,
            "cases": illegal_write_cases,
            "totalCases": total,
            "caseRate": _rate(illegal_write_cases, total),
        },
        "hospitalFactHallucinations": {
            "cases": fact_failure_cases,
            "eligibleCases": len(fact_eligible),
            "rate": _rate(fact_failure_cases, len(fact_eligible)),
        },
        "redundantToolCalls": {
            "calls": redundant_read_calls,
            "readCalls": read_calls,
            "callRate": _rate(redundant_read_calls, read_calls),
            "cases": redundant_read_cases,
            "eligibleCases": total,
            "caseRate": _rate(redundant_read_cases, total),
        },
        "pathBudgets": {
            "agentStepExceededCases": agent_step_exceeded,
            "modelRequestExceededCases": model_request_exceeded,
            "cases": budget_exceeded,
            "eligibleCases": total,
            "caseRate": _rate(budget_exceeded, total),
        },
        "emergency": {
            "truePositive": true_positive,
            "falseNegative": false_negative,
            "falsePositive": false_positive,
            "trueNegative": true_negative,
            "recall": _rate(true_positive, true_positive + false_negative),
            "falsePositiveRate": _rate(false_positive, false_positive + true_negative),
        },
        "agentSteps": _distribution(steps),
        "latencyMs": _distribution(latency),
        "firstTokenMs": {
            **_distribution(first_token),
            "missing": len(metrics) - len(first_token),
        },
        "tokens": {
            "inputMean": mean(input_tokens) if input_tokens else None,
            "outputMean": mean(output_tokens) if output_tokens else None,
        },
        "states": {name: states.get(name, 0) for name in ("completed", "failed", "interrupted")},
        "categories": {
            name: {
                "passed": sum(values),
                "total": len(values),
                "passRate": _rate(sum(values), len(values)),
            }
            for name, values in sorted(category_totals.items())
        },
    }


def build_report(
    *,
    mode: str,
    git_commit: str,
    provider: str,
    model: str,
    profile_version: str,
    concurrency: int,
    max_agent_steps: int,
    max_output_tokens: int,
    scoring_profile: Literal["strict", "semantic"],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(UTC).isoformat(),
        "mode": mode,
        "gitCommit": git_commit,
        "config": {
            "provider": provider,
            "model": model,
            "profileVersion": profile_version,
            "scoringProfile": scoring_profile,
            "caseCount": len(results),
            "concurrency": concurrency,
            "maxAgentSteps": max_agent_steps,
            "maxOutputTokens": max_output_tokens,
            "percentileMethod": "nearest-rank",
        },
        "summary": build_summary(results),
        "cases": list(results),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    config = _mapping(report.get("config"))
    summary = _mapping(report.get("summary"))
    tool_selection = _mapping(summary.get("toolSelection"))
    illegal_writes = _mapping(summary.get("illegalWrites"))
    hallucinations = _mapping(summary.get("hospitalFactHallucinations"))
    redundant_calls = _mapping(summary.get("redundantToolCalls"))
    emergency = _mapping(summary.get("emergency"))
    agent_steps = _mapping(summary.get("agentSteps"))
    path_budgets = _mapping(summary.get("pathBudgets"))
    latency = _mapping(summary.get("latencyMs"))
    tokens = _mapping(summary.get("tokens"))
    lines = [
        "# MediPet 行为评测基线",
        "",
        f"- 模式：`{report.get('mode')}`",
        f"- 模型：`{config.get('provider')}/{config.get('model')}`",
        f"- 评分层：`{config.get('scoringProfile')}`",
        f"- Git commit：`{report.get('gitCommit')}`",
        f"- 场景数：{config.get('caseCount')}",
        f"- 并发：{config.get('concurrency')}",
        f"- 最大 Agent 步数：{config.get('maxAgentSteps')}",
        f"- 最大输出 Token：{config.get('maxOutputTokens')}",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 场景通过率 | {_percent(summary.get('scenarioPassRate'))} |",
        f"| Tool 选择准确率 | {_percent(tool_selection.get('accuracy'))} |",
        f"| 非法写操作率 | {_percent(illegal_writes.get('caseRate'))} |",
        f"| 医院事实幻觉率 | {_percent(hallucinations.get('rate'))} |",
        f"| 冗余只读 Tool 调用率 | {_percent(redundant_calls.get('callRate'))} |",
        f"| 急症召回率 | {_percent(emergency.get('recall'))} |",
        f"| 急症误触发率 | {_percent(emergency.get('falsePositiveRate'))} |",
        f"| 路径预算超限场景率 | {_percent(path_budgets.get('caseRate'))} |",
        f"| 平均 Agent 步数 | {_number(agent_steps.get('mean'))} |",
        (f"| P50 / P95 延迟 | {_number(latency.get('p50'))} / {_number(latency.get('p95'))} ms |"),
        (
            f"| 平均输入 / 输出 Token | {_number(tokens.get('inputMean'))} / "
            f"{_number(tokens.get('outputMean'))} |"
        ),
        "",
        "## 分类结果",
        "",
        "| 类别 | 通过 | 通过率 |",
        "| --- | ---: | ---: |",
    ]
    for category, value in _mapping(summary.get("categories")).items():
        item = _mapping(value)
        passed = f"{item.get('passed')}/{item.get('total')}"
        lines.append(f"| {category} | {passed} | {_percent(item.get('passRate'))} |")
    lines.extend(["", "## 失败场景", ""])
    failed = [item for item in report.get("cases", []) if not item.get("passed")]
    if not failed:
        lines.append("无。")
    else:
        for item in failed:
            lines.append(f"### `{item.get('id')}`")
            lines.append("")
            for violation in item.get("violations", []):
                lines.append(f"- {violation}")
            lines.append("")
    diagnostics = [item for item in report.get("cases", []) if item.get("diagnostics")]
    lines.extend(["", "## 效率诊断", ""])
    if not diagnostics:
        lines.append("无。")
    else:
        for item in diagnostics:
            lines.append(f"### `{item.get('id')}`")
            lines.append("")
            for diagnostic in item.get("diagnostics", []):
                lines.append(f"- {diagnostic}")
            lines.append("")
    lines.extend(
        [
            "## 口径",
            "",
            "急症命中时，运行时仍记录 `completed`；本报告根据 `data-handoff` 且 "
            "`priority=emergency` 派生语义状态 `interrupted`。P50/P95 使用 nearest-rank。",
            "医院事实检查以 `capabilities/tools/fake-hospital.json` 的受控名称"
            "和结构化字段为白名单。",
            "",
        ]
    )
    return "\n".join(lines)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [str(item) for item in value]


def _contains_mapping(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def _path_value(value: Any, path: str) -> Any:
    current = value
    for segment in path.split(".") if path else []:
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _check_maximum(violations: list[str], label: str, actual: Any, maximum: Any) -> None:
    if isinstance(maximum, (int, float)) and isinstance(actual, (int, float)) and actual > maximum:
        violations.append(f"{label} was {actual}, above maximum {maximum}")


def _numbers(values: Iterable[Any]) -> list[float]:
    return [float(value) for value in values if isinstance(value, (int, float))]


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "mean": mean(values) if values else None,
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "max": max(values) if values else None,
    }


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percent(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value * 100:.1f}%"


def _number(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value:.1f}"
