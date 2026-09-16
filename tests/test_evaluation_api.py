"""评测 HTTP 接线与报告字段；不调用真实模型。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api import main
from evaluation.evaluator import EvalReport


@pytest.mark.asyncio
async def test_evaluation_endpoint_preserves_failures_and_candidate_status(monkeypatch):
    report = EvalReport("2026-09-16T10:00:00+08:00", 2, 0, 0.0, {}, [], [], [],
                        run_id="candidate-1", judge_failures=["judge-1"], call_failures=["call-1"],
                        skipped=["not-configured"], candidate_path="data/eval/candidates/candidate-1.json")
    run = AsyncMock(return_value=report)
    monkeypatch.setattr(main, "_evaluator", SimpleNamespace(run=run))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://medipet.test") as client:
        response = await client.post("/eval/run", json={"intent_cases": [], "dialog_cases": [], "boundary_cases": []})
        assert response.status_code == 200
        result = response.json()
        assert result["judge_failures"] == ["judge-1"] and result["call_failures"] == ["call-1"]
        assert result["accepted_by"] is None and result["candidate_path"].endswith("candidate-1.json")
        assert run.call_args.kwargs["boundary_cases"] == [] and run.call_args.kwargs["dialog_cases"] == []
        await client.post("/eval/run")
        assert len(run.call_args.kwargs["intent_cases"]) == 66
        assert len(run.call_args.kwargs["dialog_cases"]) == 15
        assert len(run.call_args.kwargs["boundary_cases"]) == 12
