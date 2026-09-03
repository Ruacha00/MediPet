from __future__ import annotations

import re
from collections.abc import Sequence
from functools import cache

from medipet.agent.capabilities import ParticipantToolSelection
from medipet.hospital.data_source import FakeHospitalDataSource

_MODES = {"普通": "standard", "无障碍": "accessible"}
_ROUTE_INTENT = re.compile(r"(?:怎么(?:去|到)|如何(?:去|到)|想去|要去|前往|指引|路线|从.+到)")
_INVALIDATION = re.compile(
    r"(?:并非|不在|不是|不想|不去|不要|不用|不必|无需|无意|别去|"
    r"没打算|还没确定|不确定|取消)"
)
_FOLLOWUP_MODE = re.compile(
    r"(?:我)?(?:选|要|需要|改成|改为)?(?P<mode>普通|无障碍)(?:指引|路线)?[。.]?"
)


@cache
def _catalog_aliases() -> tuple[dict[str, str], dict[str, str]]:
    source = FakeHospitalDataSource.load_default()
    origins = {
        alias: item.origin_id
        for item in source.wayfinding_origins
        for alias in (item.display_name, *item.aliases)
    }
    destinations = {
        alias: item.location_id
        for item in source.service_locations
        for alias in (item.display_name, *item.aliases)
    }
    return origins, destinations


def resolve_wayfinding_selection(
    participant_messages: Sequence[str],
) -> ParticipantToolSelection | None:
    origins, destinations = _catalog_aliases()
    route_index: int | None = None
    origin_id: str | None = None
    destination_id: str | None = None
    mode: str | None = None

    for index in range(len(participant_messages) - 1, -1, -1):
        message = participant_messages[index].strip()
        if not _ROUTE_INTENT.search(message):
            continue
        if _INVALIDATION.search(message):
            return None
        mentioned_origins = _mentioned_ids(message, origins)
        mentioned_destinations = _mentioned_ids(message, destinations)
        if len(mentioned_origins) != 1 or len(mentioned_destinations) != 1:
            return None
        route_index = index
        origin_id = mentioned_origins.pop()
        destination_id = mentioned_destinations.pop()
        route_modes = _mentioned_modes(message)
        if len(route_modes) > 1:
            return None
        mode = next(iter(route_modes), None)
        break

    if route_index is None or origin_id is None or destination_id is None:
        return None
    for message in participant_messages[route_index + 1 :]:
        stripped = message.strip()
        if _INVALIDATION.search(stripped):
            return None
        match = _FOLLOWUP_MODE.fullmatch(stripped)
        if match is None:
            return None
        mode = _MODES[match.group("mode")]
    if mode is None:
        return None
    return ParticipantToolSelection(
        tool_name="hospital_get_wayfinding_guidance",
        arguments=tuple(
            sorted(
                {
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "mode": mode,
                }.items()
            )
        ),
    )


def _mentioned_ids(message: str, aliases: dict[str, str]) -> set[str]:
    return {item_id for alias, item_id in aliases.items() if alias in message}


def _mentioned_modes(message: str) -> set[str]:
    return {value for label, value in _MODES.items() if label in message}
