from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from medipet.skills.registry import (
    SkillNotFoundError,
    SkillRegistry,
    SkillRegistryError,
)
from medipet.tools.registry import (
    ToolNotFoundError,
    ToolRegistry,
    ToolRegistryError,
)


class ConfigureToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    approval_required: bool


class BindToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    tool_version: str


def tool_management_router(
    registry: ToolRegistry,
    token: str | None,
    skill_registry: SkillRegistry | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    def require_management_token(request: Request) -> None:
        expected = token or ""
        authorization = request.headers.get("authorization")
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Management authentication failed")

    protected = [Depends(require_management_token)]

    @router.get("/tools", dependencies=protected)
    async def list_tools() -> dict[str, object]:
        return {"tools": await registry.list_tools()}

    @router.patch("/tools/{tool_id}/versions/{version}", dependencies=protected)
    async def configure_tool(
        tool_id: str, version: str, request: ConfigureToolRequest
    ) -> dict[str, object]:
        try:
            updated = await registry.configure(
                tool_id,
                version,
                **request.model_dump(),
                actor="development-admin",
            )
        except ToolNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ToolRegistryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return updated.to_dict()

    @router.get("/tool-audits", dependencies=protected)
    async def list_tool_audits() -> dict[str, object]:
        return {"audits": [audit.to_dict() for audit in await registry.list_audits()]}

    @router.post(
        "/skills/{skill_id}/versions/{skill_version}/tool-bindings",
        status_code=201,
        dependencies=protected,
    )
    async def bind_tool(
        skill_id: str, skill_version: int, request: BindToolRequest
    ) -> dict[str, object]:
        try:
            if skill_registry is None:
                raise ToolRegistryError("Skill Registry is unavailable")
            await skill_registry.assert_tool_bindings_mutable(skill_id, skill_version)
            return await registry.bind(
                skill_id,
                skill_version,
                request.tool_id,
                request.tool_version,
                actor="development-admin",
            )
        except (ToolNotFoundError, SkillNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ToolRegistryError, SkillRegistryError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
