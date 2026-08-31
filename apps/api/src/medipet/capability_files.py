from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

CAPABILITIES_PATH_ENV = "MEDIPET_CAPABILITIES_PATH"


def capabilities_root(
    environment: Mapping[str, str] | None = None,
    *,
    start: Path | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    configured = values.get(CAPABILITIES_PATH_ENV, "").strip()
    base = (start or Path.cwd()).resolve()
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    for candidate_base in (base, *base.parents):
        candidate = candidate_base / "capabilities"
        if candidate.is_dir():
            return candidate
    return base / "capabilities"


def hospital_skill_directory() -> Path:
    return hospital_skills_directory() / "hospital-appointment-assistance"


def hospital_skills_directory() -> Path:
    return capabilities_root() / "skills"


def hospital_tool_manifest_path() -> Path:
    return capabilities_root() / "tools" / "hospital.json"


def fake_hospital_data_path() -> Path:
    return capabilities_root() / "tools" / "fake-hospital.json"
