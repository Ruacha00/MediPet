from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)

HOT_RELOAD_ENV_DEFAULTS = {
    "MEDIPET_LLM_BASE_URL": "",
    "MEDIPET_LLM_API_KEY": "",
    "MEDIPET_LLM_MODEL": "",
    "MEDIPET_LLM_TEMPERATURE": "0",
    "MEDIPET_LLM_TIMEOUT_SECONDS": "30",
    "MEDIPET_TURN_TIMEOUT_SECONDS": "60",
    "MEDIPET_AGENT_MAX_STEPS": "8",
    "MEDIPET_CONTEXT_MESSAGE_LIMIT": "20",
}


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    temperature: float = 0.0
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelConfigurationError("模型 API 根地址必须是有效的 HTTP(S) URL")
        path_segments = parsed.path.strip("/").split("/")
        has_version = any(
            re.fullmatch(r"v\d+(?:\.\d+)?", segment, flags=re.IGNORECASE)
            for segment in path_segments
        )
        if not has_version or parsed.query or parsed.fragment:
            raise ModelConfigurationError("模型 API 根地址必须包含版本路径且不能包含查询或片段")
        if not self.api_key.strip() or not self.model.strip():
            raise ModelConfigurationError("模型 Token 和模型名不能为空")
        if not isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ModelConfigurationError("模型 temperature 必须在 0 到 2 之间")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ModelConfigurationError("模型超时必须大于零")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", self.api_key.strip())
        object.__setattr__(self, "model", self.model.strip())

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> ModelSettings:
        try:
            temperature = float(environment.get("MEDIPET_LLM_TEMPERATURE", "0"))
            timeout_seconds = float(environment.get("MEDIPET_LLM_TIMEOUT_SECONDS", "30"))
        except ValueError as error:
            raise ModelConfigurationError("模型数值配置无效") from error

        return cls(
            base_url=environment.get("MEDIPET_LLM_BASE_URL", ""),
            api_key=environment.get("MEDIPET_LLM_API_KEY", ""),
            model=environment.get("MEDIPET_LLM_MODEL", ""),
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class RuntimeSettings:
    model: ModelSettings
    turn_timeout_seconds: float = 60.0
    max_steps: int = 8
    context_message_limit: int = 20

    def __post_init__(self) -> None:
        if not isfinite(self.turn_timeout_seconds) or self.turn_timeout_seconds <= 0:
            raise ModelConfigurationError("turn 超时必须大于零")
        if self.max_steps <= 0:
            raise ModelConfigurationError("最大步骤数必须大于零")
        if self.context_message_limit <= 0:
            raise ModelConfigurationError("上下文消息数必须大于零")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> RuntimeSettings:
        try:
            turn_timeout_seconds = float(
                environment.get("MEDIPET_TURN_TIMEOUT_SECONDS", "60")
            )
            max_steps = int(environment.get("MEDIPET_AGENT_MAX_STEPS", "8"))
            context_message_limit = int(
                environment.get("MEDIPET_CONTEXT_MESSAGE_LIMIT", "20")
            )
        except ValueError as error:
            raise ModelConfigurationError("运行时数值配置无效") from error
        return cls(
            model=ModelSettings.from_environment(environment),
            turn_timeout_seconds=turn_timeout_seconds,
            max_steps=max_steps,
            context_message_limit=context_message_limit,
        )


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    settings: RuntimeSettings
    fingerprint: str
    sources: tuple[tuple[str, str], ...]


class RuntimeConfig(Protocol):
    def snapshot(self) -> RuntimeConfigSnapshot: ...


class DevelopmentRuntimeConfig:
    """Loads an immutable runtime profile at each development turn boundary."""

    def __init__(
        self,
        env_file: Path,
        *,
        process_environment: Mapping[str, str],
    ) -> None:
        self._env_file = env_file
        self._process_environment = dict(process_environment)
        self._fingerprint_key = secrets.token_bytes(32)
        self._last_effective: dict[str, tuple[str, str]] | None = None
        self._lock = Lock()

    def snapshot(self) -> RuntimeConfigSnapshot:
        with self._lock:
            file_environment = _read_env_file(self._env_file)
            effective, sources = _effective_runtime_environment(
                process_environment=self._process_environment,
                file_environment=file_environment,
            )
            fingerprint = _runtime_fingerprint(
                effective,
                sources,
                key=self._fingerprint_key,
            )
            observed = {
                name: (effective[name], sources[name]) for name in HOT_RELOAD_ENV_DEFAULTS
            }
            self._log_observed_change(observed, fingerprint)
            settings = RuntimeSettings.from_environment(effective)
            return RuntimeConfigSnapshot(
                settings=settings,
                fingerprint=fingerprint,
                sources=tuple(sorted(sources.items())),
            )

    def _log_observed_change(
        self,
        observed: dict[str, tuple[str, str]],
        fingerprint: str,
    ) -> None:
        previous = self._last_effective
        changed_fields = sorted(
            name
            for name, value_and_source in observed.items()
            if previous is None or previous.get(name) != value_and_source
        )
        self._last_effective = observed
        if not changed_fields:
            return
        source_names = sorted({source for _, source in observed.values()})
        LOGGER.info(
            "runtime_config_changed fingerprint=%s sources=%s changed_fields=%s",
            fingerprint,
            ",".join(source_names),
            ",".join(changed_fields),
        )


class StaticRuntimeConfig:
    """Uses a startup environment snapshot and never reads repository files."""

    def __init__(self, process_environment: Mapping[str, str]) -> None:
        self._process_environment = dict(process_environment)
        self._fingerprint_key = secrets.token_bytes(32)

    def snapshot(self) -> RuntimeConfigSnapshot:
        effective, sources = _effective_runtime_environment(
            process_environment=self._process_environment,
            file_environment={},
        )
        return RuntimeConfigSnapshot(
            settings=RuntimeSettings.from_environment(effective),
            fingerprint=_runtime_fingerprint(
                effective,
                sources,
                key=self._fingerprint_key,
            ),
            sources=tuple(sorted(sources.items())),
        )


def runtime_config_from_startup_environment(
    process_environment: Mapping[str, str],
    *,
    development_env_file: Path = Path(".env"),
) -> RuntimeConfig:
    startup_environment = dict(process_environment)
    if startup_environment.get("MEDIPET_ENVIRONMENT", "development").strip().lower() == (
        "development"
    ):
        return DevelopmentRuntimeConfig(
            development_env_file,
            process_environment=startup_environment,
        )
    return StaticRuntimeConfig(startup_environment)


def _effective_runtime_environment(
    *,
    process_environment: Mapping[str, str],
    file_environment: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    effective: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name, default in HOT_RELOAD_ENV_DEFAULTS.items():
        if name in process_environment:
            effective[name] = process_environment[name]
            sources[name] = "process"
        elif name in file_environment:
            effective[name] = file_environment[name]
            sources[name] = ".env"
        else:
            effective[name] = default
            sources[name] = "default"
    return effective, sources


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        contents = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    except (OSError, UnicodeError):
        raise ModelConfigurationError("无法读取开发环境配置文件") from None

    environment: dict[str, str] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").lstrip()
        if "=" not in stripped:
            if stripped in HOT_RELOAD_ENV_DEFAULTS:
                raise ModelConfigurationError(f"开发环境配置文件第 {line_number} 行格式无效")
            continue
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if name not in HOT_RELOAD_ENV_DEFAULTS:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ModelConfigurationError(f"开发环境配置文件第 {line_number} 行格式无效")
        environment[name] = _parse_env_value(raw_value, line_number=line_number)
    return environment


def _parse_env_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        match = re.fullmatch(r"(['\"])(.*)\1(?:\s*#.*)?", value)
        if match is None:
            raise ModelConfigurationError(f"开发环境配置文件第 {line_number} 行格式无效")
        return match.group(2)
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _runtime_fingerprint(
    effective: Mapping[str, str],
    sources: Mapping[str, str],
    *,
    key: bytes,
) -> str:
    payload = json.dumps(
        {name: [effective[name], sources[name]] for name in sorted(effective)},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:16]
