from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from api import main
from hospital.service import HospitalService
from memory.visit_store import VisitStore
from test_visit_memory import MemoryRedis
from health.reports import preprocess_report
from health.report_upload import ReportUploadError, MAX_FILE_BYTES
from test_report_preprocessing import text_pdf


@pytest_asyncio.fixture
async def report_api(monkeypatch):
    store = VisitStore(MemoryRedis(), prefix="medipet:test:reports:")
    await store.initialize_patients(HospitalService().data.patients)
    visit = await store.create_visit("anonymous", "patient_child")
    extract = AsyncMock(return_value=preprocess_report("WBC 6.2 mg/L 4-10", input_kind="pdf"))
    memory = SimpleNamespace(invalidate_working_memory=AsyncMock())
    monkeypatch.setattr(main, "_visit_store", store)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "process_report_file", extract)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, store=store, visit=visit, extract=extract, memory=memory)


async def send(ctx, **overrides):
    data = {"user_id": "anonymous", "patient_id": "patient_child", "conv_id": ctx.visit.conv_id, **overrides}
    return await ctx.client.post("/reports/preprocess", data=data, files={"file": ("report.pdf", b"%PDF-test", "application/pdf")})


@pytest.mark.asyncio
async def test_upload_binds_patient_and_persists_chat_card_without_touching_selection(report_api):
    ctx = report_api
    before = await ctx.store.get_selection("anonymous", ctx.visit.conv_id)
    response = await send(ctx)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["patient_id"] == "patient_child" and result["conv_id"] == ctx.visit.conv_id
    assert result["artifacts"][0]["type"] == "report_summary"
    assert result["extracted_text"] == "WBC 6.2 mg/L 4-10"
    history = (await ctx.client.get(f"/visits/{ctx.visit.conv_id}/messages")).json()["items"]
    assert [m["role"] for m in history] == ["user", "assistant"] and all(m["kind"] == "chat" for m in history)
    assert history[-1]["artifacts"] == result["artifacts"]
    assert await ctx.store.get_selection("anonymous", ctx.visit.conv_id) == before
    ctx.memory.invalidate_working_memory.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,status", [({"patient_id": "patient_self"}, 403), ({"user_id": "other"}, 403), ({"conv_id": "missing"}, 404)])
async def test_upload_rejects_identity_before_extraction(report_api, overrides, status):
    result = await send(report_api, **overrides)
    assert result.status_code == status
    report_api.extract.assert_not_awaited()
    assert await report_api.store.get_messages("anonymous", report_api.visit.conv_id) == []


@pytest.mark.asyncio
async def test_archived_visit_and_extraction_errors_leave_history_unchanged(report_api):
    ctx = report_api
    await ctx.store.update_visit("anonymous", ctx.visit.conv_id, archived=True)
    assert (await send(ctx)).status_code == 409
    ctx.extract.assert_not_awaited()
    await ctx.store.update_visit("anonymous", ctx.visit.conv_id, archived=False)
    ctx.extract.side_effect = ReportUploadError("encrypted_pdf", "请先解密报告。")
    result = await send(ctx)
    assert result.status_code == 422 and result.json()["detail"]["code"] == "encrypted_pdf"
    assert await ctx.store.get_messages("anonymous", ctx.visit.conv_id) == []


@pytest.mark.asyncio
async def test_archived_during_extraction_cannot_append_history(report_api):
    ctx = report_api
    async def extract(*args):
        await ctx.store.update_visit("anonymous", ctx.visit.conv_id, archived=True)
        return preprocess_report("WBC 5 mg/L 4-10")
    ctx.extract.side_effect = extract
    result = await send(ctx)
    assert result.status_code == 409
    assert await ctx.store.get_messages("anonymous", ctx.visit.conv_id) == []


@pytest.mark.asyncio
async def test_api_reads_at_most_limit_plus_one_and_reports_failure(report_api):
    ctx = report_api
    async def extract(content, *args):
        assert len(content) == MAX_FILE_BYTES + 1
        raise ReportUploadError("file_too_large", "超过 10 MB。", 413)
    ctx.extract.side_effect = extract
    result = await ctx.client.post("/reports/preprocess", data={"conv_id": ctx.visit.conv_id},
        files={"file": ("a.pdf", b"x" * (MAX_FILE_BYTES + 100), "application/pdf")})
    assert result.status_code == 413 and result.json()["detail"]["code"] == "file_too_large"


@pytest.mark.asyncio
async def test_real_pdf_worker_through_http_preserves_actual_report(report_api, monkeypatch):
    from health.report_upload import process_report_file
    monkeypatch.setattr(main, "process_report_file", process_report_file)
    result = await report_api.client.post("/reports/preprocess", data={"conv_id": report_api.visit.conv_id},
        files={"file": ("report.pdf", text_pdf(), "application/pdf")})
    assert result.status_code == 200, result.text
    card = result.json()["artifacts"][0]["data"]
    assert card["observations"][0]["value"] == "6.2" and card["observations"][0]["flag"] == "within"
    history = await report_api.store.get_messages("anonymous", report_api.visit.conv_id)
    assert history[-1].metadata["processing"] == "report_preprocessor" and "agent_type" not in history[-1].metadata
