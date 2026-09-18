"""Bounded résumé metrics run. Default prepares frozen inputs; --execute calls real API once.

Run from repository root: python -m evaluation.business_metrics --output PATH [--execute]
No credentials are collected, no baseline is accepted, no failed call is retried.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import runpy
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_inputs():
    defaults = {name: json.loads((ROOT / "evaluation/cases" / (name + ".json")).read_text(encoding="utf-8"))
                for name in ("intents", "dialogs", "boundaries")}
    variants = {
        "create-confirm": [
            ["想给孩子看儿科，请列出明天还能预约的时段。", "用列表第一项准备预约，先给我确认信息。"],
            ["请找明天可选的儿科门诊号。", "第一条就行，生成待确认预约方案。"],
            ["孩子明天要看儿科，能给我看看剩余号源吗？", "请把第一个号整理成预约确认单。"],
            ["明天儿科还有哪些号可以选？", "选择排在最前的那条，准备预约方案。"],
        ],
        "cancel-confirm": [
            ["请查看孩子现在还有效的预约。", "我要退掉第一条，请先给取消确认信息。"],
            ["帮我列出已有的有效挂号记录。", "列表首条不去了，生成待确认的取消方案。"],
            ["查一下这个就诊人的预约记录。", "准备取消第一条，展示待确认资料。"],
            ["我之前约的门诊还在吗？请展示有效预约。", "第一条需要取消，先出取消方案。"],
        ],
        "collaboration": [
            ["明天带孩子初次去儿科，帮我查可选号源，也列一下首次就诊要带的资料。", "把第一个号做成待确认方案。"],
            ["想约明天的儿科，请同时展示剩余号源和儿童初诊材料清单。", "请为第一项准备预约资料。"],
            ["帮我找明天儿科可预约的号，另外告诉我第一次带孩子到院需要准备什么。", "选择第一条，生成确认方案。"],
            ["请一并查询明天儿科号源和儿童首次就诊所需证件材料。", "请用列表第一个号生成待确认预约。"],
        ],
    }
    extras = []
    for family, pairs in variants.items():
        original = next(c for c in defaults["dialogs"]["cases"] if c["id"] == family)
        for index, turns in enumerate(pairs, 1):
            case = copy.deepcopy(original)
            case.update(id=f"{family}-new-{index}", turns=turns, clock=defaults["dialogs"]["clock"],
                        user_id="anonymous")
            extras.append({"family": family, "case": case})
    return {"defaults": defaults, "extras": extras,
            "policy": "single pass; task workload, not blind holdout; retain all failures"}


def deployment_probe(paths):
    code = """import sys,json,hashlib,pathlib,os
paths=json.load(sys.stdin)
def digest(p):
 p=pathlib.Path(p)
 return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
print(json.dumps({'sources':{p:digest('/app/'+p) for p in paths},
 'baseline_sha256':digest('/app/data/eval/baseline.json'),
 'configuration':{k:os.getenv(k) for k in ['ANTHROPIC_MODEL','MEDIPET_THINKING','MEDIPET_INTENT_EMBEDDING_BACKEND']}}))
"""
    result = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code],
                            cwd=ROOT, input=json.dumps(paths), capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


def summarize(output):
    output = Path(output)
    inputs = json.loads((output / "inputs.json").read_text(encoding="utf-8"))
    reports = []
    errors = []
    for path in sorted(output.glob("response-*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if "results" not in report:
            errors.append({"file": path.name, **report})
        else:
            reports.append(report)
    results = [r for report in reports for r in report["results"]]
    by_id = {r["test_id"]: r for r in results}
    business = [r for r in results if r["metadata"].get("kind") == "business"]
    quality = [r for r in results if r["metadata"].get("kind") == "quality"]
    intent = next((r for r in results if r["test_id"] == "intent_recognition"), {})

    def success(case_id):
        result = by_id.get(case_id + ":business", {})
        failures = [r for r in results if r["test_id"].startswith(case_id + ":") and r["metadata"].get("call_failed")]
        return result.get("passed", False) and not failures

    def group(ids):
        passed = [name for name in ids if success(name)]
        return {"passed": len(passed), "total": len(ids), "rate": len(passed) / len(ids),
                "failed_or_missing": [name for name in ids if name not in passed]}

    families = {family: [family] + [item["case"]["id"] for item in inputs["extras"] if item["family"] == family]
                for family in ("create-confirm", "cancel-confirm", "collaboration")}
    metrics = {family: group(ids) for family, ids in families.items()}
    # New IDs do not activate evaluator's special 'collaboration' first-turn rule.
    # Explicitly validate the SAME first turn here, never combine cards across turns.
    compound, sourced = [], []
    for case_id in families["collaboration"]:
        business_case = by_id.get(case_id + ":business", {})
        turns = business_case.get("metadata", {}).get("turns", [])
        first = turns[0] if turns else {}
        ok = (success(case_id) and {"appointment", "guidance"} <= set(first.get("agent_types", []))
              and {"slot_list", "visit_checklist"} <= {a["type"] for a in first.get("artifacts", [])})
        if ok:
            compound.append(case_id)
        if any(t.get("tool_name") == "search_knowledge_base" and t.get("success") is True
               and any(isinstance(s, dict) and any(s.get(k) for k in ("source", "source_id", "doc_id", "chunk_id"))
                       for s in t.get("sources", [])) for t in first.get("tool_traces", [])):
            sourced.append(case_id)
    for name, passed in (("compound_first_turn", compound), ("compound_source_presence", sourced)):
        metrics[name] = {"passed": len(passed), "total": 5, "rate": len(passed) / 5,
                         "failed_or_missing": [i for i in families["collaboration"] if i not in passed]}
    metrics["memory_scenarios"] = group(["date-followup", "period-followup", "list-selection", "patient-switch",
                                          "history-restore", "reselection", "archive-restore"])
    metrics["default_dialogs"] = group([c["id"] for c in inputs["defaults"]["dialogs"]["cases"]])
    metrics["default_boundaries"] = group([c["id"] for c in inputs["defaults"]["boundaries"]["cases"]])
    latencies = sorted(t["latency_ms"] for r in business for t in r["metadata"].get("turns", []) if "latency_ms" in t)
    summary = {"metrics": metrics, "intent_scores": intent.get("scores"),
               "intent_samples": len(intent.get("metadata", {}).get("cases", [])),
               "business": {"passed": sum(r["passed"] for r in business), "expected": 39, "observed": len(business)},
               "quality": {"passed": sum(r["passed"] for r in quality), "expected": 54, "observed": len(quality),
                           "note": "LLM judge; separate from business completion"},
               "latency": {"n": len(latencies), "p50_ms": latencies[math.ceil(len(latencies)*.5)-1] if latencies else None,
                           "p95_ms": latencies[math.ceil(len(latencies)*.95)-1] if latencies else None,
                           "scope": "observed orchestrator turns including emergency; no throughput claim"},
               "failed_results": [r for r in results if not r["passed"]], "http_errors": errors,
               "judge_failures": [v for r in reports for v in r.get("judge_failures", [])],
               "call_failures": [v for r in reports for v in r.get("call_failures", [])],
               "skipped": [v for r in reports for v in r.get("skipped", [])],
               "run_ids": [r["run_id"] for r in reports], "baseline_status": "candidate"}
    save(output / "summary.json", summary)
    return summary


def run(output, url, execute):
    output.mkdir(parents=True, exist_ok=False)
    inputs = frozen_inputs()
    save(output / "inputs.json", inputs)
    helper = runpy.run_path(str(ROOT / "tests/browser/run_evaluation.py"))
    before = helper["source_manifest"]()
    save(output / "source-manifest-before.json", before)
    save(output / "model-parameters.json", helper["model_parameters"]())
    execution = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                 "started_at": datetime.now(timezone.utc).isoformat(), "execute": execute, "url": url,
                 "inputs_sha256": sha(output / "inputs.json"), "requests": [], "retries": 0}
    save(output / "execution.json", execution)
    if not execute:
        print(json.dumps({"prepared": str(output), "real_requests": 0}))
        return
    paths = [p for p in before if p.split("/")[0] in {"agents", "api", "core", "health", "hospital", "memory", "mcp", "monitor", "skills", "knowledge"}
             or p.startswith("evaluation/cases/") or p == "evaluation/evaluator.py"]
    deployed = deployment_probe(paths)
    save(output / "deployment-before.json", deployed)
    if any(before[p] != deployed["sources"][p] for p in paths):
        raise RuntimeError("Deployed source mismatch; no model run started")
    if deployed["configuration"]["ANTHROPIC_MODEL"] != "deepseek-flash":
        raise RuntimeError("Unexpected deployed model")
    requests = [("default", None)] + [(item["case"]["id"], {"intent_cases": [], "dialog_cases": [item["case"]], "boundary_cases": []})
                                        for item in inputs["extras"]]
    try:
        for label, body in requests:
            started = time.monotonic()
            print(json.dumps({"started": label}), flush=True)
            request = urllib.request.Request(url.rstrip("/") + "/eval/run", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=1800) as response:
                    raw = response.read()
                (output / f"response-{label}.json").write_bytes(raw)
                json.loads(raw)
                event = {"case": label, "completed": True, "elapsed_seconds": round(time.monotonic()-started, 3)}
            except Exception as exc:
                event = {"case": label, "completed": False, "error_type": type(exc).__name__,
                         "elapsed_seconds": round(time.monotonic()-started, 3)}
                save(output / f"response-{label}.json", event)
            execution["requests"].append(event)
            save(output / "execution.json", execution)
            print(json.dumps(event), flush=True)
            if not event["completed"]:
                break  # Timeout may leave the server running: do not create overlapping requests.
    finally:
        after = helper["source_manifest"]()
        save(output / "source-manifest-after.json", after)
        deployed_after = deployment_probe(paths)
        save(output / "deployment-after.json", deployed_after)
        execution.update(finished_at=datetime.now(timezone.utc).isoformat(),
                         changed_source_paths=[p for p in before.keys() | after.keys() if before.get(p) != after.get(p)],
                         deployed_sources_unchanged=deployed_after["sources"] == deployed["sources"],
                         baseline_unchanged=deployed_after["baseline_sha256"] == deployed["baseline_sha256"])
        save(output / "execution.json", execution)
        summary = summarize(output)
        print(json.dumps({"metrics": summary["metrics"], "business": summary["business"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(args.output, args.url, args.execute)
