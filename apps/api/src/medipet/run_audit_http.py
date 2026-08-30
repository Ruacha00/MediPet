from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from medipet.run_audits import RunAuditStore


def run_audit_router(store: RunAuditStore, token: str | None) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    def require_management_token(request: Request) -> None:
        expected = token or ""
        authorization = request.headers.get("authorization")
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Management authentication failed")

    @router.get("/run-audits", dependencies=[Depends(require_management_token)])
    async def list_run_audits() -> dict[str, object]:
        return {"audits": [audit.to_dict() for audit in await store.list_audits()]}

    return router
