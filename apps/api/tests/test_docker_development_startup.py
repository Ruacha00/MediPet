from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_compose_configuration_defines_the_complete_safe_development_stack() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is not installed")

    result = subprocess.run(
        [docker, "compose", "config", "--format", "json"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    configuration = json.loads(result.stdout)
    assert set(configuration["services"]) == {"api", "postgres", "web"}
    api_environment = configuration["services"]["api"].get("environment", {})
    assert "MEDIPET_LLM_API_KEY" not in api_environment
    assert api_environment["MEDIPET_CAPABILITIES_PATH"] == "/app/capabilities"
    capability_mount = next(
        mount
        for mount in configuration["services"]["api"]["volumes"]
        if mount["target"] == "/app/capabilities"
    )
    assert capability_mount["type"] == "bind"
    assert capability_mount["read_only"] is True


@pytest.mark.skipif(os.name != "nt", reason="The public launcher is a Windows batch file")
def test_start_batch_launches_compose_from_the_repository_root(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker.cmd"
    fake_docker.write_text(
        "\r\n".join(
            (
                "@echo off",
                '>>"%MEDIPET_DOCKER_LOG%" echo %CD%^|%*',
                'if "%1"=="--version" exit /b 0',
                'if "%1"=="info" exit /b 0',
                'if "%1"=="compose" if "%2"=="version" exit /b 0',
                'if "%1"=="compose" if "%2"=="up" exit /b 0',
                "exit /b 91",
            )
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["MEDIPET_DOCKER_LOG"] = str(docker_log)
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        [
            os.environ["COMSPEC"],
            "/d",
            "/c",
            "call",
            str(REPOSITORY_ROOT / "start.bat"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    invocations = docker_log.read_text(encoding="utf-8").splitlines()
    assert f"{REPOSITORY_ROOT}|compose up --build" in invocations
