from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from urllib.parse import urlsplit


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
        if not 0 <= self.temperature <= 2:
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
