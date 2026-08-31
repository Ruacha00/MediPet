from __future__ import annotations

import logging

import pytest

from medipet.config import (
    DevelopmentRuntimeConfig,
    ModelConfigurationError,
    StaticRuntimeConfig,
    runtime_config_from_startup_environment,
)


def _env_text(**overrides: str) -> str:
    values = {
        "MEDIPET_LLM_BASE_URL": "https://provider.example/v1",
        "MEDIPET_LLM_API_KEY": "file-secret",
        "MEDIPET_LLM_MODEL": "model-one",
        "MEDIPET_LLM_TEMPERATURE": "0.2",
        "MEDIPET_LLM_TIMEOUT_SECONDS": "30",
        "MEDIPET_TURN_TIMEOUT_SECONDS": "60",
        "MEDIPET_AGENT_MAX_STEPS": "8",
        "MEDIPET_CONTEXT_MESSAGE_LIMIT": "20",
    }
    values.update(overrides)
    return "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"


def test_development_config_reloads_next_snapshot_and_keeps_existing_snapshot_pinned(
    tmp_path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_env_text(), encoding="utf-8")
    config = DevelopmentRuntimeConfig(
        env_file,
        process_environment={"MEDIPET_LLM_API_KEY": "process-secret"},
    )

    first = config.snapshot()
    env_file.write_text(
        _env_text(
            MEDIPET_LLM_BASE_URL="https://provider-two.example/openai/v2",
            MEDIPET_LLM_API_KEY="changed-file-secret",
            MEDIPET_LLM_MODEL="model-two",
            MEDIPET_LLM_TEMPERATURE="0.7",
            MEDIPET_LLM_TIMEOUT_SECONDS="12",
            MEDIPET_TURN_TIMEOUT_SECONDS="25",
            MEDIPET_AGENT_MAX_STEPS="5",
            MEDIPET_CONTEXT_MESSAGE_LIMIT="6",
        ),
        encoding="utf-8",
    )
    second = config.snapshot()

    assert first.settings.model.model == "model-one"
    assert first.settings.model.api_key == "process-secret"
    assert first.settings.model.temperature == 0.2
    assert first.settings.turn_timeout_seconds == 60
    assert first.settings.max_steps == 8
    assert first.settings.context_message_limit == 20
    assert second.settings.model.model == "model-two"
    assert second.settings.model.base_url == "https://provider-two.example/openai/v2"
    assert second.settings.model.api_key == "process-secret"
    assert second.settings.model.temperature == 0.7
    assert second.settings.model.timeout_seconds == 12
    assert second.settings.turn_timeout_seconds == 25
    assert second.settings.max_steps == 5
    assert second.settings.context_message_limit == 6
    assert first.fingerprint != second.fingerprint
    assert dict(second.sources)["MEDIPET_LLM_API_KEY"] == "process"


def test_invalid_reloaded_config_fails_instead_of_reusing_last_valid_snapshot(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_env_text(), encoding="utf-8")
    config = DevelopmentRuntimeConfig(env_file, process_environment={})
    valid = config.snapshot()

    env_file.write_text(
        _env_text(MEDIPET_LLM_TEMPERATURE="not-a-number"),
        encoding="utf-8",
    )

    with pytest.raises(ModelConfigurationError):
        config.snapshot()
    assert valid.settings.model.temperature == 0.2

    env_file.write_text(_env_text(MEDIPET_LLM_TEMPERATURE="0.4"), encoding="utf-8")
    assert config.snapshot().settings.model.temperature == 0.4


def test_invalid_env_file_encoding_is_a_configuration_error(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"MEDIPET_LLM_MODEL=invalid-\xff")
    config = DevelopmentRuntimeConfig(env_file, process_environment={})

    with pytest.raises(ModelConfigurationError):
        config.snapshot()


def test_config_change_log_contains_only_fingerprint_sources_and_field_names(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_env_text(), encoding="utf-8")
    config = DevelopmentRuntimeConfig(env_file, process_environment={})

    with caplog.at_level(logging.INFO, logger="medipet.config"):
        config.snapshot()
        env_file.write_text(
            _env_text(
                MEDIPET_LLM_API_KEY="new-super-secret",
                MEDIPET_LLM_MODEL="model-two",
            ),
            encoding="utf-8",
        )
        second = config.snapshot()

    assert second.settings.model.api_key == "new-super-secret"
    assert "fingerprint=" in caplog.text
    assert "source" in caplog.text
    assert "MEDIPET_LLM_API_KEY" in caplog.text
    assert "MEDIPET_LLM_MODEL" in caplog.text
    assert "file-secret" not in caplog.text
    assert "new-super-secret" not in caplog.text
    assert "https://provider.example" not in caplog.text


def test_non_hot_fields_do_not_change_the_runtime_snapshot(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        _env_text() + "MEDIPET_DATABASE_URL=postgresql://first\nMEDIPET_ENVIRONMENT=production\n",
        encoding="utf-8",
    )
    config = DevelopmentRuntimeConfig(env_file, process_environment={})
    first = config.snapshot()

    env_file.write_text(
        _env_text()
        + 'MEDIPET_DATABASE_URL="unterminated\n'
        + 'MEDIPET_MANAGEMENT_TOKEN="unterminated\n'
        + "MEDIPET_HOSPITAL_ADAPTER=changed\n"
        + "MEDIPET_ENVIRONMENT=test\n",
        encoding="utf-8",
    )
    second = config.snapshot()

    assert second == first


def test_production_and_test_startup_config_never_watch_the_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_env_text(MEDIPET_LLM_MODEL="file-model"), encoding="utf-8")
    process_environment = {
        "MEDIPET_ENVIRONMENT": "production",
        "MEDIPET_LLM_BASE_URL": "https://production.example/v1",
        "MEDIPET_LLM_API_KEY": "production-secret",
        "MEDIPET_LLM_MODEL": "production-model",
    }

    config = runtime_config_from_startup_environment(
        process_environment,
        development_env_file=env_file,
    )
    first = config.snapshot()
    env_file.write_text(_env_text(MEDIPET_LLM_MODEL="changed-file-model"), encoding="utf-8")
    process_environment["MEDIPET_LLM_MODEL"] = "changed-process-model"
    second = config.snapshot()

    assert isinstance(config, StaticRuntimeConfig)
    assert first == second
    assert second.settings.model.model == "production-model"


def test_development_config_reads_the_runtime_env_file_selected_at_startup(tmp_path) -> None:
    env_file = tmp_path / "deepseek.env"
    env_file.write_text(_env_text(MEDIPET_LLM_MODEL="deepseek-chat"), encoding="utf-8")

    config = runtime_config_from_startup_environment(
        {
            "MEDIPET_ENVIRONMENT": "development",
            "MEDIPET_RUNTIME_ENV_FILE": str(env_file),
        }
    )

    assert config.snapshot().settings.model.model == "deepseek-chat"
