"""Real Redis tool-boundary verification with explicit arguments, no model calls."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from redis.asyncio import Redis

from agents.agent_orchestrator import Request
from agents.tools import build_hospital_tools
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.visit_store import VisitStore


async def run(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Use a new evidence path")
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    prefix = "medipet:boundary-check:" + uuid4().hex + ":"
    now = lambda: datetime.fromisoformat("2026-09-16T10:00:00+08:00")
    visits = VisitStore(client, prefix=prefix, clock=now)
    service = HospitalService(store=HospitalStore(client, prefix), visit_store=visits, clock=now)
    result = {"storage": "real Redis, own random prefix", "model_calls": 0, "checks": {}}
    try:
        await visits.initialize_patients(service.data.patients)
        await service.initialize_slots()
        own = await visits.create_visit("anonymous", "patient_self")
        child = await visits.create_visit("anonymous", "patient_child")
        req = Request(message="查明天内科号源", user_id=own.user_id, patient_id=own.patient_id, conv_id=own.conv_id)
        tool = build_hospital_tools("appointment", service, visits)["search_slots"]
        await tool.handler(req, {"department": "内科", "date": "明天"})
        before = await visits.get_selection(req.user_id, req.conv_id)
        declined = replace(req, message="我只想了解号源查询规则和首次材料，不用实际查询号源。")
        denied = await tool.handler(declined, {"department": "眼科", "date": "后天"})
        after = await visits.get_selection(req.user_id, req.conv_id)
        result["checks"]["declined_query_keeps_selection"] = not denied["success"] and before == after
        result["checks"]["other_patient_untouched"] = (await visits.get_selection(child.user_id, child.conv_id)).slots == []
        checklist = build_hospital_tools("guidance", service, visits)["get_visit_checklist"]
        own_materials = await checklist.handler(replace(req, message="孩子已经就诊过了，现在给我本人查询明天内科号源和首次就诊材料。"),
                                               {"department": "内科", "visit_type": "first"})
        child_materials = await checklist.handler(replace(req, user_id=child.user_id, patient_id=child.patient_id, conv_id=child.conv_id,
                                                         message="儿童首次就诊材料"), {"department": "儿科", "visit_type": "first"})
        result["checks"]["current_self_first"] = own_materials["success"] and own_materials["data"]["visit_type"] == "first"
        result["checks"]["explicit_child_corrected"] = child_materials["success"] and child_materials["data"]["visit_type"] == "child"
        result.update(declined_result=denied, before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
                      own_materials=own_materials, child_materials=child_materials)
        assert all(result["checks"].values())
    finally:
        keys = [key async for key in client.scan_iter(match=prefix + "*")]
        if keys:
            await client.delete(*keys)
        result["owned_keys_remaining"] = len([key async for key in client.scan_iter(match=prefix + "*")])
        await client.aclose()
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["checks"]), flush=True)


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1])))
