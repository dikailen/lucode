from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from runtime.providers.openai_compatible import OpenAICompatibleProvider


@dataclass
class ProviderRegistry:
    """Small provider factory and SDK-client cache boundary.

    The first MVP only delegates OpenAI-compatible creation, while keeping a stable
    extension point for Anthropic/Gemini/Bedrock adapters later.
    """

    _providers: dict[str, Any] = field(default_factory=dict)
    _model_cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._providers:
            provider = OpenAICompatibleProvider()
            self._providers = {
                "openai_compatible": provider,
                "openai": provider,
                "ollama": provider,
            }

    def create_model(
        self,
        *,
        provider_id: str,
        sdk_type: str,
        api_key: str,
        base_url: str,
        model_name: str,
        options: dict[str, Any] | None = None,
    ):
        normalized_sdk = normalize_sdk_type(sdk_type)
        provider = self._providers.get(normalized_sdk)
        if provider is None:
            raise ValueError(f"暂不支持 Provider SDK 类型：{sdk_type}")

        cache_key = self._cache_key(
            provider_id=provider_id,
            sdk_type=normalized_sdk,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            options=options or {},
        )
        if cache_key not in self._model_cache:
            self._model_cache[cache_key] = provider.create_model(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                options=options or {},
            )
        return self._model_cache[cache_key]

    def cache_size(self) -> int:
        return len(self._model_cache)

    @staticmethod
    def _cache_key(
        *,
        provider_id: str,
        sdk_type: str,
        api_key: str,
        base_url: str,
        model_name: str,
        options: dict[str, Any],
    ) -> str:
        auth_fingerprint = hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:12]
        payload = {
            "provider_id": str(provider_id or ""),
            "sdk_type": str(sdk_type or ""),
            "auth": auth_fingerprint,
            "base_url": str(base_url or ""),
            "model_name": str(model_name or ""),
            "options": options,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def normalize_sdk_type(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"", "openai_compatible", "openai_compat", "openai"}:
        return "openai_compatible"
    if normalized == "ollama":
        return "ollama"
    return normalized
