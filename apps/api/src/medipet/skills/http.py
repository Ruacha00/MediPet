from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from medipet.skills.archive import SkillArchiveError
from medipet.skills.registry import (
    LifecycleAction,
    SkillNotFoundError,
    SkillRegistry,
    SkillRegistryError,
)

LIFECYCLE_ACTIONS: tuple[LifecycleAction, ...] = (
    "submit_review",
    "publish",
    "retire",
    "activate",
)


class CreateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str
    instructions: str
    change_note: str
    skill_type: Literal["instruction-only", "tool-assisted"] = "instruction-only"


class EditSkillRequest(BaseModel):
    instructions: str
    change_note: str


def management_router(registry: SkillRegistry, token: str | None) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    def require_management_token(request: Request) -> None:
        expected = token or ""
        authorization = request.headers.get("authorization")
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="管理认证失败")

    protected = [Depends(require_management_token)]

    @router.get("/skills", dependencies=protected)
    async def list_skills() -> dict[str, object]:
        return {"skills": await registry.list_skills()}

    @router.post("/skills", status_code=201, dependencies=protected)
    async def create_skill(request: CreateSkillRequest) -> dict[str, object]:
        try:
            version = await registry.create_skill(**request.model_dump(), actor="development-admin")
        except (SkillArchiveError, SkillRegistryError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return version.to_dict()

    @router.post("/skills/import", status_code=201, dependencies=protected)
    async def import_skill(request: Request) -> dict[str, object]:
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/zip":
            await registry.record_rejection("reject_import", actor="development-admin")
            raise HTTPException(status_code=415, detail="Skill 导入仅接受 application/zip")
        try:
            version = await registry.import_package(await request.body(), actor="development-admin")
        except (SkillArchiveError, SkillRegistryError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return version.to_dict()

    @router.get(
        "/skills/{skill_id}/versions/{version}/export",
        dependencies=protected,
        response_class=Response,
    )
    async def export_skill(skill_id: str, version: int) -> Response:
        try:
            filename, content = await registry.export_package(
                skill_id, version, actor="development-admin"
            )
        except SkillNotFoundError as error:
            await registry.record_rejection("reject_export", actor="development-admin")
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SkillArchiveError as error:
            await registry.record_rejection("reject_export", actor="development-admin")
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.patch("/skills/{skill_id}", status_code=201, dependencies=protected)
    async def edit_skill(skill_id: str, request: EditSkillRequest) -> dict[str, object]:
        try:
            version = await registry.edit_skill(
                skill_id, **request.model_dump(), actor="development-admin"
            )
        except SkillNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (SkillArchiveError, SkillRegistryError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return version.to_dict()

    def add_transition_route(action: LifecycleAction) -> None:
        async def transition_skill(skill_id: str, version: int) -> dict[str, object]:
            try:
                updated = await registry.transition(
                    skill_id,
                    version,
                    action,
                    actor="development-admin",
                )
            except SkillNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except SkillRegistryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            return updated.to_dict()

        router.add_api_route(
            f"/skills/{{skill_id}}/versions/{{version}}/{action.replace('_', '-')}",
            transition_skill,
            methods=["POST"],
            name=f"{action}_skill_version",
            dependencies=protected,
        )

    for lifecycle_action in LIFECYCLE_ACTIONS:
        add_transition_route(lifecycle_action)

    @router.get("/skill-audits", dependencies=protected)
    async def list_skill_audits() -> dict[str, list[dict[str, object]]]:
        return {"audits": [audit.to_dict() for audit in await registry.list_audits()]}

    return router
