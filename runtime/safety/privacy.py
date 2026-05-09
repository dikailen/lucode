from __future__ import annotations

import os
from dataclasses import dataclass


PRIVACY_MODES = {"offline", "local_first", "cloud_allowed"}
NETWORK_MCP_IDS = {"web_search"}
CLOUD_BACKENDS = {"openai", "openai_compatible"}
LOCAL_BACKENDS = {"ollama", "llama_cpp"}
OFFLINE_NETWORK_POLICY = {"warn", "block"}


@dataclass(frozen=True)
class PrivacyPolicy:
    """Runtime privacy policy for model and network selection."""

    mode: str = "local_first"

    @classmethod
    def from_env(cls) -> "PrivacyPolicy":
        raw = os.environ.get("AGENTS_PRIVACY_MODE") or "local_first"
        return cls(normalize_privacy_mode(raw))

    @property
    def allows_cloud_models(self) -> bool:
        return self.mode != "offline"

    @property
    def allows_network_tools(self) -> bool:
        if self.mode != "offline":
            return True
        return self.offline_network_policy() != "block"

    def model_allowed(self, model_info: dict) -> bool:
        backend_type = normalize_backend_type(model_info.get("backend_type") or "")
        if self.mode == "offline":
            return backend_type in LOCAL_BACKENDS or bool(model_info.get("is_local"))
        return True

    def mcp_allowed(self, mcp_id: str) -> bool:
        if self.mode != "offline":
            return True
        if mcp_id not in NETWORK_MCP_IDS:
            return True
        return self.offline_network_policy() != "block"

    def model_error(self, model_id: str, model_info: dict) -> str:
        backend_type = model_info.get("backend_type") or "unknown"
        return f"隐私模式 offline 禁止使用云端模型：{model_id}（backend={backend_type}）"

    def mcp_error(self, mcp_id: str) -> str:
        return f"隐私模式 offline 禁止使用联网 MCP：{mcp_id}"

    def mcp_warning(self, mcp_id: str) -> str:
        if self.offline_network_policy() == "block":
            return self.mcp_error(mcp_id)
        return (
            f"隐私模式 offline 下请求了联网 MCP：{mcp_id}。"
            "该工具可能会把查询内容发送到外部网络；请仅在你明确需要联网并理解风险时批准执行。"
        )

    def offline_network_policy(self) -> str:
        raw = str(os.environ.get("AGENTS_OFFLINE_NETWORK_MCP_POLICY") or "warn").strip().lower()
        if raw not in OFFLINE_NETWORK_POLICY:
            return "warn"
        return raw

    def sort_model_ids(self, model_ids: list[str], model_infos: dict[str, dict]) -> list[str]:
        if self.mode != "local_first":
            return list(model_ids)
        indexed = list(enumerate(model_ids))
        indexed.sort(
            key=lambda item: (
                0 if model_infos.get(item[1], {}).get("is_local") else 1,
                item[0],
            )
        )
        return [model_id for _, model_id in indexed]


def normalize_privacy_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in PRIVACY_MODES:
        return "local_first"
    return mode


def normalize_backend_type(value: str) -> str:
    backend = str(value or "").strip().lower().replace("-", "_")
    if backend in {"openai_compat", "openai-compatible", "compatible"}:
        return "openai_compatible"
    if backend in {"ollama_http", "ollama_api"}:
        return "ollama"
    if backend in {"llamacpp", "llama.cpp", "gguf"}:
        return "llama_cpp"
    if backend in {"openai", "openai_compatible", "ollama", "llama_cpp"}:
        return backend
    return "openai_compatible"


def infer_backend_type(base_url: str = "", provider: str = "", explicit: str = "") -> str:
    if explicit:
        return normalize_backend_type(explicit)

    provider_lower = str(provider or "").lower()
    base_lower = str(base_url or "").lower()
    if provider_lower in {"ollama", "local", "local_ollama"}:
        return "ollama"
    if provider_lower in {"llama_cpp", "llamacpp", "gguf"}:
        return "llama_cpp"
    if "localhost:11434" in base_lower or "127.0.0.1:11434" in base_lower:
        return "ollama"
    if provider_lower == "openai":
        return "openai"
    return "openai_compatible"


def privacy_level_for_backend(backend_type: str) -> str:
    backend = normalize_backend_type(backend_type)
    if backend == "llama_cpp":
        return "local_native"
    if backend == "ollama":
        return "local"
    return "cloud"


def is_local_backend(backend_type: str) -> bool:
    return normalize_backend_type(backend_type) in LOCAL_BACKENDS
