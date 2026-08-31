from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from medipet.capability_files import CAPABILITIES_PATH_ENV, capabilities_root
from medipet.hospital.bootstrap import load_hospital_skill_source
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalOperations
from medipet.hospital.tool_manifest import load_hospital_tool_specs
from medipet.hospital.tools import HospitalToolProvider
from medipet.tools.registry import ToolRegistryError

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_ROOT = REPOSITORY_ROOT / "capabilities"


def test_capability_definitions_live_outside_application_source() -> None:
    assert (CAPABILITIES_ROOT / "skills/hospital-appointment-assistance/SKILL.md").is_file()
    assert (CAPABILITIES_ROOT / "skills/hospital-appointment-cancellation/SKILL.md").is_file()
    assert (CAPABILITIES_ROOT / "skills/hospital-service-catalog/SKILL.md").is_file()
    assert (CAPABILITIES_ROOT / "tools/hospital.json").is_file()
    assert (CAPABILITIES_ROOT / "tools/fake-hospital.json").is_file()
    assert not (
        REPOSITORY_ROOT
        / "apps/api/src/medipet/hospital/appointment_skill/SKILL.md"
    ).exists()
    assert not (REPOSITORY_ROOT / "apps/api/src/medipet/hospital/fake_hospital.json").exists()


def test_capabilities_root_accepts_an_external_mount(tmp_path: Path) -> None:
    configured = capabilities_root(
        {CAPABILITIES_PATH_ENV: "mounted-capabilities"},
        start=tmp_path,
    )

    assert configured == (tmp_path / "mounted-capabilities").resolve()


async def test_tool_contract_changes_are_loaded_from_the_external_manifest(
    tmp_path: Path,
) -> None:
    source_path = CAPABILITIES_ROOT / "tools/hospital.json"
    manifest = json.loads(source_path.read_text(encoding="utf-8"))
    manifest["tools"][0]["description"] = "由外部挂载清单提供的说明。"
    mounted_manifest = tmp_path / "hospital.json"
    mounted_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 8, 31, tzinfo=UTC),
    )

    tools = await HospitalToolProvider(
        operations,
        manifest_path=mounted_manifest,
    ).tools()

    assert tools[0].description == "由外部挂载清单提供的说明。"
    assert "$ref" not in json.dumps(tools[0].output_schema)


async def test_external_manifest_cannot_downgrade_a_write_executor(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (CAPABILITIES_ROOT / "tools/hospital.json").read_text(encoding="utf-8")
    )
    create_tool = manifest["tools"][-1]
    create_tool["effect"] = "read"
    create_tool["approval_required"] = False
    create_tool.pop("confirmation_schema")
    mounted_manifest = tmp_path / "hospital.json"
    mounted_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 8, 31, tzinfo=UTC),
    )

    with pytest.raises(ToolRegistryError, match="trusted executor"):
        await HospitalToolProvider(
            operations,
            manifest_path=mounted_manifest,
        ).tools()


def test_skill_and_tool_manifests_load_from_explicit_external_paths() -> None:
    skill = load_hospital_skill_source(
        CAPABILITIES_ROOT / "skills/hospital-appointment-assistance"
    )
    tools = load_hospital_tool_specs(CAPABILITIES_ROOT / "tools/hospital.json")

    assert skill.slug == "hospital-appointment-assistance"
    assert [tool.tool_id for tool in tools][-1] == "hospital.cancel_appointment"
