from __future__ import annotations

from medipet.agent.capabilities import CapabilitySnapshot, ToolContext
from medipet.skills.registry import SkillRegistry
from medipet.tools.registry import ToolRegistry


class RegistryCapabilityProvider:
    def __init__(
        self,
        registry: SkillRegistry,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._tool_registry = tool_registry

    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot:
        skills = await self._registry.published_skills(context)
        tools = (
            await self._tool_registry.runtime_tools(skills, context)
            if self._tool_registry is not None
            else ()
        )
        return CapabilitySnapshot(
            skill_versions=tuple(f"{skill.slug}@{skill.version}" for skill in skills),
            skills=skills,
            tools=tools,
        )
