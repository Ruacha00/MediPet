"""Real Redis appointment invariants, bounded to 50 duplicate pairs and 30 races.

Run inside the deployed API container with PYTHONPATH=/app. Uses REDIS_URL without
printing it, fixed clock and unique keys. Never clears shared business records.
"""
import asyncio
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from redis.asyncio import Redis
from hospital.models import VisitIdentity
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.visit_store import VisitStore

CLOCK = datetime.fromisoformat("2026-09-18T10:00:00+08:00")


def compact(result):
    return {"success": result.success, "error_code": result.error_code,
            "receipt_id": (result.data or {}).get("receipt_id"),
            "appointment_id": (result.data or {}).get("appointment", {}).get("appointment_id")}


def require(result):
    if not result.success:
        raise RuntimeError(result.error_code)
    return result.data


async def simultaneous(service, pairs):
    gate = asyncio.Event()

    async def call(identity, proposal_id):
        await gate.wait()
        start = time.perf_counter()
        result = compact(await service.confirm_proposal(identity, proposal_id))
        result["latency_ms"] = (time.perf_counter() - start) * 1000
        return result

    tasks = [asyncio.create_task(call(*pair)) for pair in pairs]
    gate.set()
    return await asyncio.gather(*tasks)


async def one_group(client, kind, index):
    prefix = f"medipet:metrics:{uuid4().hex}:"
    row = {"kind": kind, "index": index, "prefix": prefix, "passed": False}
    visits = VisitStore(client, prefix=prefix, clock=lambda: CLOCK)
    service = HospitalService(store=HospitalStore(client, prefix), visit_store=visits, clock=lambda: CLOCK)
    try:
        await visits.initialize_patients(service.data.patients)
        require(await service.initialize_slots())

        async def identity():
            visit = await visits.create_visit("anonymous", "patient_child")
            return VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)

        slots = require(await service.search_slots(department="儿科", date="明天"))["slots"]
        slot_id = slots[0]["slot_id"]
        initial = slots[0]["remaining"]
        if kind == "duplicate":
            who = await identity()
            proposal = require(await service.prepare_appointment(who, slot_id=slot_id))
            row["create_results"] = await simultaneous(service, [(who, proposal["proposal_id"])] * 10)
            # One explicit later replay per phase, not an automatic retry of all failed calls.
            created = require(await service.confirm_proposal(who, proposal["proposal_id"]))
            row["create_replay"] = compact(await_result(created))
            records = await service.store.get_appointments(who.user_id, who.patient_id)
            remaining = (await service.store.get_slot(slot_id)).remaining
            create_receipts = {r["receipt_id"] for r in row["create_results"] if r["success"]}
            row["create_facts"] = {"records": len(records), "active": sum(r.status == "active" for r in records),
                                   "initial_remaining": initial, "remaining": remaining,
                                   "receipt_matches_replay": create_receipts == {created["receipt_id"]}}
            create_ok = (len(records) == 1 and records[0].status == "active" and remaining == initial-1
                         and create_receipts == {created["receipt_id"]})
            cancellation = require(await service.prepare_cancellation(who, created["appointment"]["appointment_id"]))
            row["cancel_results"] = await simultaneous(service, [(who, cancellation["proposal_id"])] * 10)
            cancelled = require(await service.confirm_proposal(who, cancellation["proposal_id"]))
            row["cancel_replay"] = compact(await_result(cancelled))
            records = await service.store.get_appointments(who.user_id, who.patient_id)
            remaining = (await service.store.get_slot(slot_id)).remaining
            cancel_receipts = {r["receipt_id"] for r in row["cancel_results"] if r["success"]}
            row["cancel_facts"] = {"records": len(records), "cancelled": sum(r.status == "cancelled" for r in records),
                                   "remaining": remaining, "receipt_matches_replay": cancel_receipts == {cancelled["receipt_id"]}}
            row["passed"] = (create_ok and len(records) == 1 and records[0].status == "cancelled" and remaining == initial
                             and cancel_receipts == {cancelled["receipt_id"]})
        else:
            slot = await service.store.get_slot(slot_id)
            await client.set(service.store.slot_key(slot_id), slot.model_copy(update={"remaining": 1}).model_dump_json())
            pairs = []
            for _ in range(10):
                who = await identity()
                proposal = require(await service.prepare_appointment(who, slot_id=slot_id))
                pairs.append((who, proposal["proposal_id"]))
            row["race_results"] = await simultaneous(service, pairs)
            records = await service.store.get_appointments("anonymous", "patient_child")
            remaining = (await service.store.get_slot(slot_id)).remaining
            row["facts"] = {"initial_remaining": 1, "remaining": remaining, "records": len(records),
                            "active": sum(r.status == "active" for r in records),
                            "successful_requests": sum(r["success"] for r in row["race_results"])}
            row["passed"] = remaining == 0 and len(records) == 1 and records[0].status == "active" and row["facts"]["successful_requests"] == 1
    except Exception as exc:
        row["error_type"] = type(exc).__name__
    finally:
        keys = [key async for key in client.scan_iter(match=prefix + "*")]
        if keys:
            await client.delete(*keys)
        row["remaining_owned_keys"] = len([key async for key in client.scan_iter(match=prefix + "*")])
    return row


def await_result(data):
    # Represent already returned replay data without making another service request.
    from types import SimpleNamespace
    return SimpleNamespace(success=True, error_code=None, data=data)


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"started_at": datetime.now(timezone.utc).isoformat(), "clock": CLOCK.isoformat(),
                "python": sys.version, "platform": platform.platform(), "concurrency": 10,
                "duplicate_groups": 50, "race_groups": 30, "model_calls": 0,
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "sources": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                            for p in ["hospital/service.py", "hospital/store.py", "hospital/models.py", "memory/visit_store.py"]}}
    (output / "configuration.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    rows = []
    try:
        for kind, count in [("duplicate", 50), ("race", 30)]:
            for index in range(count):
                row = await one_group(client, kind, index)
                rows.append(row)
                with (output / "results.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        await client.aclose()
    attempts = [r for row in rows for key in ("create_results", "cancel_results", "race_results") for r in row.get(key, [])]
    summary = {kind: {"passed": sum(row["passed"] for row in rows if row["kind"] == kind), "total": count}
               for kind, count in [("duplicate", 50), ("race", 30)]}
    summary.update(concurrent_attempts=len(attempts), successful_concurrent_attempts=sum(r["success"] for r in attempts),
                   errors={code: sum(r["error_code"] == code for r in attempts) for code in sorted({r["error_code"] for r in attempts if r["error_code"]})},
                   explicit_replays=sum(key in row for row in rows for key in ("create_replay", "cancel_replay")),
                   cleanup_ok=all(row["remaining_owned_keys"] == 0 for row in rows),
                   failed_groups=[{"kind": row["kind"], "index": row["index"], "error_type": row.get("error_type")} for row in rows if not row["passed"]],
                   finished_at=datetime.now(timezone.utc).isoformat())
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1])))
