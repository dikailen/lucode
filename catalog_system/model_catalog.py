from __future__ import annotations

from pathlib import Path

from catalog_system.model_probe import cached_probe_for_model, model_fingerprint
from runtime.agents.model_capability import strategy_for_model_info
from runtime.config.model_config import (
    configured_provider_model_definitions,
    lucode_config_signature,
    workspace_root_path,
)
from runtime.config.model_selection import model_runtime_available
from runtime.providers.registry import ProviderRegistry, normalize_sdk_type
from runtime.safety.privacy import (
    PrivacyPolicy,
    infer_backend_type,
    is_local_backend,
    privacy_level_for_backend,
)


_MODEL_CATALOG_CACHE: dict[str, object] = {"signature": None, "catalog": None}


def discover_model_definitions() -> list[dict]:
    """Discover models configured through Lucode provider config only."""

    definitions: list[dict] = []
    known_ids: set[str] = set()
    for item in configured_provider_model_definitions():
        model_id = str(item.get("id") or "")
        if model_id and model_id not in known_ids:
            definitions.append(item)
            known_ids.add(model_id)
    return definitions


def _config_bool_value(value) -> bool | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disable", "disabled"}


def _supports_tools_from_config_or_guess(value, model_name: str, backend_type: str) -> bool:
    if isinstance(value, bool):
        return value
    explicit = _config_bool_value(value)
    if explicit is not None:
        return explicit
    name = str(model_name or "").lower()
    backend = str(backend_type or "").lower()
    if backend == "ollama" and any(marker in name for marker in ["deepseek-r1", "reasoning", "r1:"]):
        return False
    return True


def load_model_catalog(force_reload: bool = False) -> dict:
    """Build a model catalog from Lucode provider configuration."""

    signature = _model_catalog_signature()
    if not force_reload and _MODEL_CATALOG_CACHE.get("signature") == signature:
        cached = _MODEL_CATALOG_CACHE.get("catalog")
        if isinstance(cached, dict):
            return cached

    catalog = _build_model_catalog()
    _MODEL_CATALOG_CACHE["signature"] = signature
    _MODEL_CATALOG_CACHE["catalog"] = catalog
    return catalog


def clear_model_catalog_cache() -> None:
    _MODEL_CATALOG_CACHE["signature"] = None
    _MODEL_CATALOG_CACHE["catalog"] = None


def _model_runtime_available(model_info: dict) -> bool:
    return model_runtime_available(model_info)


def _build_model_catalog() -> dict:
    models = []
    project_root = workspace_root_path()
    for item in discover_model_definitions():
        api_key = item.get("api_key_value") or ""
        base_url = item.get("base_url_value") or ""
        model_name = item.get("model_name_value") or item.get("default_model_name") or ""
        backend_type = infer_backend_type(
            base_url or "",
            item.get("provider") or "",
            item.get("backend_type") or "",
        )
        is_local = is_local_backend(backend_type)
        configured = bool(base_url and model_name and (api_key or is_local))
        supports_tools = _supports_tools_from_config_or_guess(
            item.get("supports_tools"),
            model_name or "",
            backend_type,
        )
        strategy = strategy_for_model_info(
            {
                "id": item["id"],
                "display_name_zh": item["display_name_zh"],
                "model_name": model_name or "",
                "reasoning_level": item["reasoning_level"],
                "cost_level": item["cost_level"],
            }
        )

        models.append(
            _merge_probe(
                project_root,
                {
                    "id": item["id"],
                    "display_name_zh": item["display_name_zh"],
                    "provider": item["provider"],
                    "configured": configured,
                    "base_url_configured": bool(base_url),
                    "base_url": base_url or "",
                    "model_name": model_name or "",
                    "backend_type": backend_type,
                    "is_local": is_local,
                    "privacy_level": privacy_level_for_backend(backend_type),
                    "supports_tools": supports_tools,
                    "strengths": item["strengths"],
                    "best_for_skills": item["best_for_skills"],
                    "cost_level": item["cost_level"],
                    "reasoning_level": item["reasoning_level"],
                    "model_tier": strategy.tier.value,
                    "execution_strategy": strategy.to_dict(),
                    "shared_config_group": item.get("shared_config_group") or "",
                    "model_alias": item.get("model_alias") or "",
                    "source": item.get("source") or "lucode_config",
                    "provider_ref": item.get("provider_ref") or "",
                    "homepage": item.get("homepage") or "",
                    "probe_fingerprint": model_fingerprint(
                        {
                            "id": item["id"],
                            "backend_type": backend_type,
                            "base_url": base_url or "",
                            "model_name": model_name or "",
                        }
                    ),
                },
            )
        )

    return {
        "version": 1,
        "selection_rules": [
            "Only select models with configured=true and allowed by the current privacy mode.",
            "Local models require a runnable probe result before they are considered available.",
            "Tasks that require tools must select supports_tools=true models.",
            "Choose by model catalog metadata such as best_for_skills, strengths, reasoning_level, and cost_level.",
            "If no configured model satisfies the task, do not invent a model id; fall back to direct answer or ask for configuration.",
        ],
        "models": models,
    }


def _model_catalog_signature() -> tuple:
    probe_file = workspace_root_path() / ".agent_cache" / "model_capabilities.json"
    return (
        _file_signature(probe_file),
        lucode_config_signature(),
    )


def _file_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def compact_model_catalog_for_prompt(allowed_ids=None) -> str:
    catalog = load_model_catalog()
    allowed = {str(item or "").strip() for item in (allowed_ids or []) if str(item or "").strip()}
    lines = ["Model catalog (select configured=true models only):"]
    if allowed:
        lines.append(
            "注意：用户已设定员工可用模型池，只能从下列 id 中为 task.model 选择，不得使用池外模型。"
        )
    for item in catalog.get("models", []):
        if allowed and str(item.get("id") or "") not in allowed:
            continue
        lines.append(
            "- "
            f"{item['id']} | "
            f"configured:{item.get('configured')} | "
            f"name:{item.get('model_name') or 'unconfigured'} | "
            f"backend:{item.get('backend_type')} | "
            f"privacy:{item.get('privacy_level')} | "
            f"tier:{item.get('model_tier')} | "
            f"tools:{item.get('supports_tools')} | "
            f"strengths:{','.join(item.get('strengths') or [])} | "
            f"best_for:{','.join(item.get('best_for_skills') or []) or 'general'} | "
            f"cost:{item.get('cost_level')} | "
            f"reasoning:{item.get('reasoning_level')}"
        )
    return "\n".join(lines)


class ModelRegistry:
    """Create model objects by id using the current Lucode provider configuration."""

    def __init__(self):
        self.definitions = self._load_definitions()
        self.provider_registry = ProviderRegistry()

    def refresh(self) -> None:
        self.definitions = self._load_definitions()

    def _load_definitions(self) -> dict[str, dict]:
        return {item["id"]: item for item in discover_model_definitions()}

    def _ensure_definition(self, model_id: str) -> None:
        if model_id in self.definitions:
            return
        self.refresh()

    def get_model(self, model_id: str):
        self._ensure_definition(model_id)
        if model_id not in self.definitions:
            raise KeyError(f"Unknown model id: {model_id}")

        item = self.definitions[model_id]
        api_key = item.get("api_key_value") or ""
        base_url = item.get("base_url_value") or ""
        model_name = item.get("model_name_value") or item.get("default_model_name") or ""
        backend_type = infer_backend_type(
            base_url or "",
            item.get("provider") or "",
            item.get("backend_type") or "",
        )
        is_local = is_local_backend(backend_type)

        if not base_url or not model_name or (not api_key and not is_local):
            raise ValueError(f"Model is not fully configured: {model_id}")

        sdk_type = normalize_sdk_type(item.get("sdk_type") or item.get("compatible_type") or backend_type)
        return self.provider_registry.create_model(
            provider_id=item.get("provider") or model_id,
            sdk_type=sdk_type,
            api_key=api_key or "local-model-no-api-key",
            base_url=base_url,
            model_name=model_name,
            options=item.get("options") or {},
        )

    def get_model_info(self, model_id: str) -> dict:
        catalog = load_model_catalog()
        for item in catalog.get("models", []):
            if item.get("id") == model_id:
                return item
        clear_model_catalog_cache()
        catalog = load_model_catalog(force_reload=True)
        for item in catalog.get("models", []):
            if item.get("id") == model_id:
                self._ensure_definition(model_id)
                return item
        raise KeyError(f"Unknown model id: {model_id}")

    def first_configured(self, preferred: list[str], privacy_mode: str = "local_first") -> str:
        policy = PrivacyPolicy(privacy_mode)
        catalog = load_model_catalog()
        model_infos = {item["id"]: item for item in catalog["models"]}
        configured = {
            item["id"]
            for item in catalog["models"]
            if item["configured"] and model_runtime_available(item) and policy.model_allowed(item)
        }
        for model_id in policy.sort_model_ids(preferred, model_infos):
            if model_id in configured:
                return model_id
        if configured:
            return sorted(configured)[0]
        raise ValueError(f"No configured models allowed by privacy mode: {policy.mode}")


def _merge_probe(project_root: Path, model_info: dict) -> dict:
    merged = dict(model_info)
    probe = cached_probe_for_model(project_root, model_info)
    merged["probe"] = probe or {}
    if probe:
        for key in [
            "supports_tools",
            "supports_basic_chat",
            "supports_json_output",
            "planner_suitable",
            "execution_suitable",
            "latency_ms",
            "chat_latency_ms",
            "context_window_tokens",
            "context_tier",
            "context_source",
            "recommended_roles",
        ]:
            if key in probe and probe.get(key) is not None:
                merged[key] = probe.get(key)
    return merged
