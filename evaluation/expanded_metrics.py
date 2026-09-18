"""Execute a frozen 80-task workload through the deployed isolated evaluator."""
import argparse
import hashlib
import json
import runpy
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from evaluation.business_metrics import ROOT, deployment_probe, save


def score(item, report):
    case_id, family, expected = item["case"]["id"], item["family"], item["expected"]
    results = report.get("results", [])
    business = next((r for r in results if r["test_id"] == case_id + ":business"), {})
    metadata = business.get("metadata", {})
    turns = metadata.get("turns", [])
    checks = {"business_assertions": bool(business.get("passed")),
              "no_call_failure": not report.get("call_failures") and bool(results)}
    if family in {"create", "cancel"}:
        confirmations = [a.get("receipt", {}) for a in metadata.get("actions", []) if a["action"] == "confirm_current"]
        receipt = confirmations[-1] if confirmations else {}
        record = receipt.get("appointment", {})
        slot = record.get("snapshot", {}).get("slot", {})
        checks.update(receipt_operation=receipt.get("operation") == family,
                      patient=record.get("patient_id") == expected["patient_id"],
                      department=slot.get("department_id") == expected["department_id"],
                      date=slot.get("date") == expected["date"])
        if family == "create":
            lists = [a["data"]["slots"] for t in turns[:1] for a in t.get("artifacts", []) if a["type"] == "slot_list"]
            index = expected["selection_index"]
            checks["chosen_slot"] = bool(lists and len(lists[-1]) > index and slot.get("slot_id") == lists[-1][index]["slot_id"])
    elif family == "compound":
        artifacts = turns[0].get("artifacts", []) if turns else []
        lists = [a["data"] for a in artifacts if a["type"] == "slot_list"]
        checklists = [a["data"] for a in artifacts if a["type"] == "visit_checklist"]
        checks["matching_slots"] = any(l.get("slots") and all(s.get("department_id") == expected["department_id"] and s.get("date") == expected["date"] and s.get("remaining", 0) > 0 for s in l["slots"]) for l in lists)
        checks["nonempty_checklist"] = any(c.get("items") and c.get("source", {}).get("source_id") and c.get("department_id") in (None, expected["department_id"]) and (expected["patient_id"] != "patient_child" or c.get("visit_type") == "child") for c in checklists)
    return {"id": case_id, "family": family, "passed": all(checks.values()), "checks": checks,
            "dual_roles": bool(turns and {"appointment", "guidance"} <= set(turns[0].get("agent_types", []))) if family == "compound" else None,
            "quality_passed": sum(r["passed"] for r in results if r.get("metadata", {}).get("kind") == "quality"),
            "quality_observed": sum(r.get("metadata", {}).get("kind") == "quality" for r in results),
            "expected_turns": len(item["case"]["turns"]),
            "failed_results": [{"test_id": r["test_id"], "detail": r.get("detail"), "scores": r.get("scores"), "assertions": r.get("metadata", {}).get("assertions")} for r in results if not r["passed"]]}


def aggregate(items, reports):
    rows = [score(item, reports.get(item["case"]["id"], {})) for item in items]
    groups = {}
    for family in ("create", "cancel", "compound", "context"):
        subset = [r for r in rows if r["family"] == family]
        passed = sum(r["passed"] for r in subset)
        groups[family] = {"passed": passed, "total": len(subset), "rate": passed / len(subset),
                          "failed": [r["id"] for r in subset if not r["passed"]]}
    return {"groups": groups, "booking_completion": {"passed": groups["create"]["passed"]+groups["cancel"]["passed"], "total":40,
                                                      "rate":(groups["create"]["passed"]+groups["cancel"]["passed"])/40},
            "quality": {"passed":sum(r["quality_passed"] for r in rows), "observed":sum(r["quality_observed"] for r in rows), "expected":sum(r["expected_turns"] for r in rows)},
            "dual_role_compound": {"passed": sum(r["dual_roles"] is True for r in rows), "total":20},
            "call_failures": [v for report in reports.values() for v in report.get("call_failures", [])],
            "judge_failures": [v for report in reports.values() for v in report.get("judge_failures", [])],
            "rows":rows}


def run(inputs_path, output, execute):
    output.mkdir(parents=True, exist_ok=False)
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    save(output / "inputs.json", inputs)
    helper = runpy.run_path(str(ROOT / "tests/browser/run_evaluation.py"))
    before = helper["source_manifest"]()
    save(output / "source-manifest-before.json", before)
    save(output / "model-parameters.json", helper["model_parameters"]())
    execution = {"started_at": datetime.now(timezone.utc).isoformat(), "head":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
                 "inputs_sha256":hashlib.sha256((output/"inputs.json").read_bytes()).hexdigest(), "requests":[], "retries":0}
    save(output / "execution.json", execution)
    if not execute:
        return
    paths = [p for p in before if p.split("/")[0] in {"agents","api","core","health","hospital","memory","mcp","monitor","skills","knowledge"} or p=="evaluation/evaluator.py"]
    deployed = deployment_probe(paths)
    save(output / "deployment-before.json", deployed)
    if any(deployed["sources"][p] != before[p] for p in paths):
        raise RuntimeError("Deployed source mismatch")
    reports = {}
    try:
        for index, item in enumerate(inputs["business"], 1):
            case = item["case"]
            body = {"intent_cases":[], "dialog_cases":[case], "boundary_cases":[]}
            started = time.monotonic()
            request = urllib.request.Request("http://127.0.0.1:8000/eval/run", data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    raw=response.read()
                (output / (case["id"]+".json")).write_bytes(raw)
                report=json.loads(raw)
                reports[case["id"]]=report
                event={"id":case["id"],"completed":True,"elapsed_seconds":round(time.monotonic()-started,3)}
            except Exception as exc:
                event={"id":case["id"],"completed":False,"error_type":type(exc).__name__}
            execution["requests"].append(event)
            save(output / "execution.json", execution)
            save(output / "summary.json", aggregate(inputs["business"],reports))
            print(json.dumps({"index":index,**event,"score":score(item,reports.get(case["id"],{}))["passed"]}),flush=True)
            if not event["completed"]:
                break
    finally:
        after=helper["source_manifest"]()
        save(output / "source-manifest-after.json", after)
        deployed_after=deployment_probe(paths)
        save(output / "deployment-after.json", deployed_after)
        execution.update(finished_at=datetime.now(timezone.utc).isoformat(),changed_source_paths=[p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)],
                         deployed_sources_unchanged=deployed["sources"]==deployed_after["sources"], baseline_unchanged=deployed["baseline_sha256"]==deployed_after["baseline_sha256"])
        save(output / "execution.json",execution)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args()
    run(args.inputs,args.output,args.execute)
