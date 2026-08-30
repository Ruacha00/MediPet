from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from medipet.hospital.bootstrap import bootstrap_development_hospital_skill
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalOperations
from medipet.hospital.tools import HospitalToolProvider
from medipet.persistence.conversation import DevelopmentVisitMatter
from medipet.persistence.postgres import (
    DatabaseConfigurationError,
    PostgresVisitConversationStore,
)
from medipet.skills.postgres import PostgresSkillRegistry
from medipet.tools.postgres import PostgresToolRegistry

DEVELOPMENT_VISIT_MATTER = DevelopmentVisitMatter(
    patient_id="patient-demo",
    patient_display_name="演示患者",
    participant_id="participant-demo",
    participant_display_name="患者本人",
    visit_matter_id="visit-matter-demo",
    visit_matter_title="初次咨询",
)


async def seed() -> None:
    database_url = os.getenv("MEDIPET_DATABASE_URL", "")
    if not database_url:
        raise DatabaseConfigurationError("缺少 MEDIPET_DATABASE_URL")
    store = PostgresVisitConversationStore.from_url(database_url)
    tool_registry = PostgresToolRegistry.from_url(database_url)
    skill_registry = PostgresSkillRegistry.from_url(
        database_url,
        tool_registry=tool_registry,
    )
    try:
        await store.seed_development_visit_matter(DEVELOPMENT_VISIT_MATTER)
        operations = FakeHospitalOperations(
            FakeHospitalDataSource.load_default(),
            clock=lambda: datetime.now(UTC),
        )
        await bootstrap_development_hospital_skill(
            skill_registry,
            tool_registry,
            HospitalToolProvider(operations),
        )
    finally:
        await skill_registry.close()
        await tool_registry.close()
        await store.close()


def main() -> None:
    asyncio.run(seed())
    print("Development visit matter and hospital Skill are ready.")


if __name__ == "__main__":
    main()
