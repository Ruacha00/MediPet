from __future__ import annotations

import asyncio
import os

from medipet.persistence.conversation import DevelopmentVisit
from medipet.persistence.postgres import (
    DatabaseConfigurationError,
    PostgresVisitConversationStore,
)

DEVELOPMENT_VISIT = DevelopmentVisit(
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
    try:
        await store.seed_development_visit(DEVELOPMENT_VISIT)
    finally:
        await store.close()


def main() -> None:
    asyncio.run(seed())
    print("Development patient, participant, and visit matter are ready.")


if __name__ == "__main__":
    main()
