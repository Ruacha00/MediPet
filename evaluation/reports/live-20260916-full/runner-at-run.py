"""Run one complete evaluation from the real page, retaining candidate evidence.

Without --execute, only inspect local inputs and source parameters. Coordinate the
code/Skills/data freeze before execution; a failure never triggers another run.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_COUNTS = {"intent": 54, "dialog": 12, "boundary": 12}
CASE_FILES = {"intent": "intents.json", "dialog": "dialogs.json", "boundary": "boundaries.json"}
CONFIG_NAMES = {
    "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "MEDIPET_THINKING",
    "MEDIPET_COMPOSER_MAX_TOKENS", "MEDIPET_COMPOSER_TEMPERATURE",
    "MEDIPET_SKILLS_MAX_PROMPT_CHARS",
}
SOURCE_SUFFIXES = {".py", ".json", ".md", ".txt", ".vue", ".js", ".css"}
LLM_FILES = (
    "agents/agent_orchestrator.py", "core/intent_recognizer.py",
    "memory/conversation_memory.py", "mcp/tool_manager.py", "evaluation/evaluator.py",
)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def source_manifest():
    paths = set()
    for directory in ("agents", "api", "core", "hospital", "memory", "mcp", "monitor",
                      "skills", "knowledge", "evaluation/cases", "frontend/src", "frontend/public"):
        paths.update(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and p.suffix in SOURCE_SUFFIXES and "__pycache__" not in p.parts)
    paths.update((ROOT / "evaluation").glob("*.py"))
    for name in ("requirements.txt", "requirements-dev.txt", "Dockerfile", "docker-compose.yml",
                 "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.js",
                 "frontend/nginx.conf", "frontend/Dockerfile", "frontend/index.html",
                 "tests/browser/run_evaluation.py"):
        paths.add(ROOT / name)
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths) if p.is_file()}


def local_configuration():
    """Read only named non-secret fields; never collect a key or connection URL."""
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() in CONFIG_NAMES:
                values[name.strip()] = value.strip().strip("\"'")
    return {"source": "local .env allowlist; process settings confirmed separately by coordinator",
            "values": values}


def model_parameters():
    """Keep parameter expressions and role defaults, without loading application code."""
    entries = []
    for filename in LLM_FILES:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            params = {kw.arg: ast.unparse(kw.value) for kw in node.keywords
                      if kw.arg in {"max_tokens", "temperature"}}
            is_create = isinstance(node.func, ast.Attribute) and node.func.attr == "create"
            if params or is_create:
                entries.append({"file": filename, "line": node.lineno,
                                "call": ast.unparse(node.func), "parameters": params,
                                "expanded_kwargs": [ast.unparse(kw.value) for kw in node.keywords if kw.arg is None]})
    return {"configuration": local_configuration(), "source_parameter_expressions": entries,
            "note": "Role defaults feed request_kwargs; returned report independently records model and Judge parameters."}


def local_inputs():
    return {kind: json.loads((ROOT / "evaluation/cases" / name).read_text(encoding="utf-8"))
            for kind, name in CASE_FILES.items()}


def preflight():
    inputs = local_inputs()
    counts = {kind: len(value["cases"]) for kind, value in inputs.items()}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected default inputs: {counts}")
    config = local_configuration()["values"]
    if config.get("ANTHROPIC_MODEL") != "deepseek-flash" or config.get("MEDIPET_THINKING") != "disabled":
        raise ValueError("Expected local model deepseek-flash with MEDIPET_THINKING=disabled")
    return inputs, counts


def report_summary(report):
    metadata = report.get("metadata", {})
    results = report.get("results", [])
    intent = next((r for r in results if r.get("metadata", {}).get("kind") == "intent"), {})
    intent_cases = intent.get("metadata", {}).get("cases", [])
    return {
        "run_id": report.get("run_id"), "baseline_status": metadata.get("baseline_status"),
        "accepted_by": report.get("accepted_by"), "model": metadata.get("model"),
        "case_counts": metadata.get("case_counts"), "input_sha256": metadata.get("input_sha256"),
        "result_total": report.get("total"), "result_passed": report.get("passed"),
        "result_pass_rate": report.get("pass_rate"), "avg_scores": report.get("avg_scores"),
        "intent_scores": intent.get("scores", {}),
        "intent_mismatches": [item for item in intent_cases if item.get("predicted") != item.get("expected")],
        "valid_quality_count": metadata.get("valid_quality_count"),
        "business_failures": metadata.get("business_failures", []),
        "judge_failures": report.get("judge_failures", []),
        "call_failures": report.get("call_failures", []), "skipped": report.get("skipped", []),
        "failed_results": [{"test_id": item.get("test_id"), "detail": item.get("detail"),
                            "scores": item.get("scores"), "metadata": item.get("metadata")}
                           for item in results if not item.get("passed")],
        "candidate_path": report.get("candidate_path"),
    }


def run_once(url, output, timeout_seconds):
    from playwright.sync_api import expect, sync_playwright

    inputs, counts = preflight()
    output.mkdir(parents=True, exist_ok=False)
    initial_sources = source_manifest()
    save_json(output / "source-manifest-before.json", initial_sources)
    save_json(output / "inputs.json", inputs)
    save_json(output / "model-parameters.json", model_parameters())
    baseline = ROOT / "data/eval/baseline.json"
    baseline_before = digest(baseline) if baseline.exists() else None
    evidence = {
        "started_at": stamp(), "base_url": url, "expected_case_counts": counts,
        "expected_api": "http://127.0.0.1:8010", "request_method": "POST", "request_body": None,
        "page_errors": [], "evaluation_requests": [], "timeout_seconds": timeout_seconds,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "baseline_before_sha256": baseline_before, "formal_request_completed": False,
        "baseline_policy": "candidate only; no acceptance call", "retries": 0,
    }
    save_json(output / "execution.json", evidence)
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))

        def track_request(request):
            if urlparse(request.url).path.endswith("/eval/run") and request.method == "POST":
                evidence["evaluation_requests"].append({"url": request.url, "method": request.method,
                                                        "body": request.post_data, "at": stamp()})
                save_json(output / "execution.json", evidence)

        page.on("request", track_request)
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="评测", exact=True).click()
            expect(page.get_by_role("button", name="运行评测", exact=True)).to_be_enabled()
            page.screenshot(path=str(output / "before.png"), full_page=True)
            evidence["clicked_at"] = stamp()
            print(json.dumps({"phase": "request_started", "at": evidence["clicked_at"],
                              "counts": counts, "output": str(output)}, ensure_ascii=False), flush=True)
            started = time.monotonic()
            with page.expect_response(
                lambda response: urlparse(response.url).path.endswith("/eval/run")
                and response.request.method == "POST", timeout=timeout_seconds * 1000,
            ) as pending:
                page.get_by_role("button", name="运行评测", exact=True).click()
            response = pending.value
            evidence.update(http_status=response.status, response_url=response.url,
                            response_elapsed_seconds=round(time.monotonic() - started, 3))
            body = response.body()
            (output / "response.json").write_bytes(body)
            evidence["response_sha256"] = hashlib.sha256(body).hexdigest()
            report = json.loads(body)
            evidence["formal_request_completed"] = True
            save_json(output / "summary.json", report_summary(report))
            if not response.ok:
                raise RuntimeError(f"Evaluation returned HTTP {response.status}; response retained")
            if report.get("metadata", {}).get("case_counts") != EXPECTED_COUNTS:
                raise RuntimeError("Response does not cover the expected complete input sets")
            if report.get("metadata", {}).get("model") != "deepseek-flash":
                raise RuntimeError("Returned model differs from the frozen configuration")
            if report.get("metadata", {}).get("baseline_status") != "candidate" or report.get("accepted_by"):
                raise RuntimeError("Report is not an unaccepted candidate")
            if len(evidence["evaluation_requests"]) != 1 or evidence["evaluation_requests"][0]["body"]:
                raise RuntimeError("Expected exactly one default request with no replacement cases")
            expect(page.locator(".baseline-status")).to_contain_text(report["run_id"], timeout=30000)
            expect(page.locator(".baseline-status")).to_have_attribute("data-baseline-status", "candidate")
            page.screenshot(path=str(output / "report-full.png"), full_page=True)
            page.get_by_label("只看失败或跳过").check()
            failed = page.locator(".evaluation-case")
            if failed.count():
                failed.first.locator("summary").click()
            page.screenshot(path=str(output / "report-failures.png"), full_page=True)
            evidence["page_report_verified"] = True
        except Exception as error:
            evidence["error"] = f"{type(error).__name__}: {error}"
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            evidence["finished_at"] = stamp()
            evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
            final_sources = source_manifest()
            save_json(output / "source-manifest-after.json", final_sources)
            evidence["changed_source_paths"] = sorted(name for name in initial_sources.keys() | final_sources.keys()
                                                       if initial_sources.get(name) != final_sources.get(name))
            evidence["baseline_after_sha256"] = digest(baseline) if baseline.exists() else None
            evidence["accepted_baseline_unchanged"] = evidence["baseline_after_sha256"] == baseline_before
            (output / "page.html").write_text(page.content(), encoding="utf-8")
            (output / "page.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
            save_json(output / "execution.json", evidence)
            browser.close()
    if evidence["changed_source_paths"] or not evidence["accepted_baseline_unchanged"] or evidence["page_errors"]:
        raise RuntimeError("Source freeze, baseline preservation, or browser check failed; evidence retained")
    print(json.dumps({"phase": "complete", "elapsed_seconds": evidence["elapsed_seconds"],
                      "output": str(output), "report": str(output / "response.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=1800, choices=range(1, 1801), metavar="1..1800")
    parser.add_argument("--execute", action="store_true", help="click the real evaluation button once, after coordinated freeze")
    args = parser.parse_args()
    if args.execute:
        destination = args.output or ROOT / "evaluation/reports" / datetime.now().strftime("live-%Y%m%d-%H%M%S")
        run_once(args.url, destination, args.timeout_seconds)
    else:
        _, case_counts = preflight()
        print(json.dumps({"phase": "prepared_only", "case_counts": case_counts,
                          "source_files": len(source_manifest()), "real_requests": 0}, ensure_ascii=False))
