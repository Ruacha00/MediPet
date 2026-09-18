"""Scoring guards for task outcomes, separate from the paid model measurements."""
import copy

from evaluation.expanded_cases import business_cases, retrieval_cases
from evaluation.expanded_metrics import aggregate, score


def business_report(item, *, actions=None, artifacts=None, roles=None):
    return {"results":[{"test_id":item["case"]["id"]+":business","passed":True,
                        "metadata":{"kind":"business","actions":actions or [],
                                    "turns":[{"artifacts":artifacts or [],"agent_types":roles or []}]}}]}


def test_frozen_workload_has_expected_denominators_and_labels():
    cases=business_cases()
    assert {f:sum(c["family"]==f for c in cases) for f in ["create","cancel","compound","context"]}==dict.fromkeys(["create","cancel","compound","context"],20)
    assert len(retrieval_cases())==40
    summary=aggregate(cases,{})
    assert summary["booking_completion"]=={"passed":0,"total":40,"rate":0}
    assert summary["quality"]["expected"]==145
    assert all(g["total"]==20 and g["passed"]==0 for g in summary["groups"].values())


def test_wrong_patient_receipt_does_not_pass_on_inventory_assertions():
    item=next(c for c in business_cases() if c["family"]=="cancel")
    receipt={"operation":"cancel","appointment":{"patient_id":"wrong-patient","snapshot":{"slot":{"department_id":item["expected"]["department_id"],"date":item["expected"]["date"]}}}}
    result=score(item,business_report(item,actions=[{"action":"confirm_current","receipt":receipt}]))
    assert not result["passed"] and result["checks"]["business_assertions"]
    assert not result["checks"]["patient"]


def test_compound_empty_or_wrong_patient_checklist_is_not_delivery():
    item=next(c for c in business_cases() if c["family"]=="compound")
    expected=item["expected"]
    slots={"type":"slot_list","data":{"slots":[{"department_id":expected["department_id"],"date":expected["date"],"remaining":2}]}}
    checklist={"type":"visit_checklist","data":{"items":[],"source":{"source_id":"child-materials"},"visit_type":"child"}}
    assert not score(item,business_report(item,artifacts=[slots,checklist]))["passed"]
    checklist["data"]["items"]=["儿童身份证明"]
    # A single role can satisfy the user's task if it actually delivers both correct cards.
    result=score(item,business_report(item,artifacts=[slots,checklist],roles=["appointment"]))
    assert result["passed"] and not result["dual_roles"]
    checklist["data"]["visit_type"]="first"
    assert not score(item,business_report(item,artifacts=[slots,checklist]))["passed"]
