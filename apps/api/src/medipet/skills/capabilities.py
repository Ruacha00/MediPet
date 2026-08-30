from __future__ import annotations

from medipet.agent.capabilities import CapabilitySnapshot, ToolContext
from medipet.skills.registry import SkillRegistry


class RegistryCapabilityProvider:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot:
        skills = await self._registry.published_skills(context)
        return CapabilitySnapshot(
            skill_versions=tuple(f"{skill.slug}@{skill.version}" for skill in skills),
            skills=skills,
        )
