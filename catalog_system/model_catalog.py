import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from catalog_system.model_probe import cached_probe_for_model, model_fingerprint
from runtime.agents.model_capability import strategy_for_model_info
from runtime.providers.registry import ProviderRegistry, normalize_sdk_type
from runtime.safety.privacy import (
    PrivacyPolicy,
    infer_backend_type,
    is_local_backend,
    privacy_level_for_backend,
)
from runtime.config.model_config import configured_provider_model_definitions, lucode_config_signature


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
_MODEL_CATALOG_CACHE: dict[str, object] = {"signature": None, "catalog": None}


KNOWN_MODEL_DEFINITIONS = [
    {
        "id": "deepseek_V4_flash_model",
        "display_name_zh": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "shared_api_key_env": "MODEL_DEEPSEEK_API_KEY",
        "shared_base_url_env": "MODEL_DEEPSEEK_BASE_URL",
        "default_model_name": "deepseek-v4-flash",
        "strengths": ["中文解释", "项目分析", "文本处理", "低成本常规任务"],
        "best_for_skills": ["project_explorer", "humanizer_zh"],
        "cost_level": "low",
        "reasoning_level": "medium",
        "backend_type": "openai_compatible",
    },
    {
        "id": "deepseek_V4_pro_model",
        "display_name_zh": "DeepSeek V4 Pro",
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_pro_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_pro_URL",
        "model_env": "DEEPSEEK_pro_MODEL",
        "shared_api_key_env": "MODEL_DEEPSEEK_API_KEY",
        "shared_base_url_env": "MODEL_DEEPSEEK_BASE_URL",
        "default_model_name": "deepseek-v4-pro",
        "strengths": ["复杂规划", "Skill 创建", "多任务拆分", "汇总判断"],
        "best_for_skills": ["skill_creator", "orchestrator_planner", "final_synthesizer"],
        "cost_level": "high",
        "reasoning_level": "high",
        "backend_type": "openai_compatible",
    },
    {
        "id": "mimo_model",
        "display_name_zh": "MiMo v2.5 Pro",
        "provider": "mimo",
        "api_key_env": "MIMO_API_KEY",
        "base_url_env": "MIMO_API_BASE_URL",
        "model_env": "MIMO_API_MODEL",
        "strengths": ["代码生成", "代码解释", "代码评审", "排查报错"],
        "best_for_skills": ["jpc_now_skill"],
        "cost_level": "medium",
        "reasoning_level": "medium",
        "backend_type": "openai_compatible",
    },
]

MODEL_GROUP_SUFFIXES = [
    "BEST_FOR_SKILLS",
    "REASONING_LEVEL",
    "DISPLAY_PREFIX",
    "DISPLAY_NAME",
    "SUPPORTS_TOOLS",
    "COST_LEVEL",
    "BASE_URL",
    "API_KEY",
    "PROVIDER",
    "STRENGTHS",
    "BACKEND",
    "MODELS",
    "MODEL",
    "NAME",
]

COMPAT_GROUP_DEFINITIONS = {
    "DEEPSEEK": {
        "provider": "deepseek",
        "api_key_env": "MODEL_DEEPSEEK_API_KEY",
        "base_url_env": "MODEL_DEEPSEEK_BASE_URL",
        "models_env": "MODEL_DEEPSEEK_MODELS",
        "display_prefix": "DeepSeek",
        "backend_type": "openai_compatible",
        "strengths": ["中文解释", "项目分析", "复杂规划", "文本处理"],
        "best_for_skills": ["project_explorer", "humanizer_zh", "skill_creator", "orchestrator_planner"],
    },
    "MIMO": {
        "provider": "mimo",
        "api_key_env": "MIMO_API_KEY",
        "base_url_env": "MIMO_API_BASE_URL",
        "model_env": "MIMO_API_MODEL",
        "models_env": "MIMO_API_MODELS",
        "display_prefix": "MiMo",
        "backend_type": "openai_compatible",
        "strengths": ["代码生成", "代码解释", "代码评审", "排查报错"],
        "best_for_skills": ["jpc_now_skill"],
    },
}


def _normalize_model_id(raw_id: str) -> str:
    value = raw_id.strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value.endswith("_model"):
        value += "_model"
    return value


def _env_keys() -> set[str]:
    keys = set(os.environ)
    env_file = dotenv_values(BASE_DIR / ".env")
    keys.update(env_file.keys())
    return {key for key in keys if key}


def _model_groups_from_env() -> set[str]:
    groups = set()
    suffixes = sorted(MODEL_GROUP_SUFFIXES, key=len, reverse=True)
    for key in _env_keys():
        if not key.startswith("MODEL_"):
            continue
        rest = key.removeprefix("MODEL_")
        for suffix in suffixes:
            token = f"_{suffix}"
            if rest.endswith(token):
                group = rest[: -len(token)]
                if group:
                    groups.add(group)
                break
    return groups


def discover_model_definitions() -> list[dict]:
    """Discover known models plus generic MODEL_<ID>_* entries from .env."""

    definitions = []
    known_ids = set()
    env_keys = _env_keys()

    for item in configured_provider_model_definitions():
        if item["id"] not in known_ids:
            definitions.append(item)
            known_ids.add(item["id"])

    for item in KNOWN_MODEL_DEFINITIONS:
        if _known_model_is_registered(item, env_keys):
            definitions.append(dict(item))
            known_ids.add(item["id"])

    for group, config in COMPAT_GROUP_DEFINITIONS.items():
        if config.get("models_env") and os.getenv(config["models_env"]):
            for item in _shared_model_definitions_for_group(group, os.getenv(config["models_env"]) or "", config):
                if item["id"] not in known_ids:
                    definitions.append(item)
                    known_ids.add(item["id"])

    for group in sorted(_model_groups_from_env()):
        if group in COMPAT_GROUP_DEFINITIONS:
            continue
        shared_models = os.getenv(f"MODEL_{group}_MODELS") or ""
        if shared_models.strip():
            for item in _shared_model_definitions_for_group(group, shared_models):
                if item["id"] not in known_ids:
                    definitions.append(item)
                    known_ids.add(item["id"])
            continue

        model_env = f"MODEL_{group}_MODEL"
        if model_env not in env_keys:
            model_env = f"MODEL_{group}_NAME"
        if model_env not in env_keys:
            continue

        model_id = _normalize_model_id(group)
        if model_id in known_ids:
            continue

        display_name = os.getenv(f"MODEL_{group}_DISPLAY_NAME") or group.replace("_", " ").title()
        provider = os.getenv(f"MODEL_{group}_PROVIDER") or group.lower()
        strengths = _split_env_list(os.getenv(f"MODEL_{group}_STRENGTHS")) or ["通用任务"]
        best_for_skills = _split_env_list(os.getenv(f"MODEL_{group}_BEST_FOR_SKILLS"))
        backend_type = infer_backend_type(
            os.getenv(f"MODEL_{group}_BASE_URL") or "",
            provider,
            os.getenv(f"MODEL_{group}_BACKEND") or "",
        )
        configured_model_name = os.getenv(model_env) or ""

        definitions.append(
            {
                "id": model_id,
                "display_name_zh": display_name,
                "provider": provider,
                "api_key_env": f"MODEL_{group}_API_KEY",
                "base_url_env": f"MODEL_{group}_BASE_URL",
                "model_env": model_env,
                "model_name_value": "",
                "supports_tools_env": f"MODEL_{group}_SUPPORTS_TOOLS",
                "strengths": strengths,
                "best_for_skills": best_for_skills,
                "cost_level": os.getenv(f"MODEL_{group}_COST_LEVEL") or "medium",
                "reasoning_level": os.getenv(f"MODEL_{group}_REASONING_LEVEL") or "medium",
                "backend_type": backend_type,
                "supports_tools": _supports_tools_from_env_or_guess(
                    os.getenv(f"MODEL_{group}_SUPPORTS_TOOLS"),
                    configured_model_name,
                    backend_type,
                ),
            }
        )
        known_ids.add(model_id)

    return definitions


def _known_model_is_registered(item: dict, env_keys: set[str]) -> bool:
    if item.get("provider") == "deepseek" and os.getenv("MODEL_DEEPSEEK_MODELS"):
        return False
    if item.get("provider") == "mimo" and os.getenv("MIMO_API_MODELS"):
        return False
    if item.get("model_env") in env_keys:
        return True
    shared_api = item.get("shared_api_key_env")
    shared_base = item.get("shared_base_url_env")
    if shared_api and shared_base and shared_api in env_keys and shared_base in env_keys:
        return bool(item.get("default_model_name"))
    return False


def _shared_model_definitions_for_group(group: str, models_value: str, group_config: dict | None = None) -> list[dict]:
    group_config = group_config or {}
    provider = os.getenv(f"MODEL_{group}_PROVIDER") or group_config.get("provider") or group.lower()
    api_key_env = group_config.get("api_key_env") or f"MODEL_{group}_API_KEY"
    base_url_env = group_config.get("base_url_env") or f"MODEL_{group}_BASE_URL"
    backend_type = infer_backend_type(
        os.getenv(base_url_env) or "",
        provider,
        os.getenv(f"MODEL_{group}_BACKEND") or group_config.get("backend_type") or "",
    )
    strengths = _split_env_list(os.getenv(f"MODEL_{group}_STRENGTHS")) or list(group_config.get("strengths") or ["通用任务"])
    best_for_skills = _split_env_list(os.getenv(f"MODEL_{group}_BEST_FOR_SKILLS")) or list(group_config.get("best_for_skills") or [])
    display_prefix = os.getenv(f"MODEL_{group}_DISPLAY_PREFIX") or group_config.get("display_prefix") or group.replace("_", " ").title()
    definitions = []

    for alias, model_name in _parse_shared_model_specs(models_value):
        model_id = _shared_model_id(group, alias)
        definitions.append(
            {
                "id": model_id,
                "display_name_zh": f"{display_prefix} {alias}",
                "provider": provider,
                "api_key_env": api_key_env,
                "base_url_env": base_url_env,
                "model_env": "",
                "model_name_value": model_name,
                "supports_tools_env": f"MODEL_{group}_SUPPORTS_TOOLS",
                "strengths": strengths,
                "best_for_skills": best_for_skills,
                "cost_level": os.getenv(f"MODEL_{group}_COST_LEVEL") or "medium",
                "reasoning_level": os.getenv(f"MODEL_{group}_REASONING_LEVEL") or "medium",
                "backend_type": backend_type,
                "supports_tools": _supports_tools_from_env_or_guess(
                    os.getenv(f"MODEL_{group}_SUPPORTS_TOOLS"),
                    model_name,
                    backend_type,
                ),
                "shared_config_group": group,
                "model_alias": alias,
            }
        )

    return definitions


def _shared_model_id(group: str, alias: str) -> str:
    group_prefix = _model_alias_from_name(group)
    clean_alias = _model_alias_from_name(alias)
    if clean_alias == group_prefix or clean_alias.startswith(f"{group_prefix}_"):
        return _normalize_model_id(clean_alias)
    return _normalize_model_id(f"{group_prefix}_{clean_alias}")


def _parse_shared_model_specs(value: str) -> list[tuple[str, str]]:
    specs = []
    for raw in re.split(r"[,;\n]+", value or ""):
        text = raw.strip()
        if not text:
            continue
        alias = ""
        model_name = text
        if "=" in text:
            alias, model_name = [part.strip() for part in text.split("=", 1)]
        elif ":" in text and not _looks_like_plain_model_tag(text):
            alias, model_name = [part.strip() for part in text.split(":", 1)]
        if not alias:
            alias = _model_alias_from_name(model_name)
        alias = _model_alias_from_name(alias)
        if model_name:
            specs.append((alias, model_name))
    return specs


def _looks_like_plain_model_tag(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9.-]+:[A-Za-z0-9._-]+", value.strip()))


def _model_alias_from_name(value: str) -> str:
    alias = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower())
    alias = re.sub(r"_+", "_", alias).strip("_")
    return alias or "model"


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool_value(value: str | None) -> bool | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disable", "disabled"}


def _supports_tools_from_env_or_guess(value: str | None, model_name: str, backend_type: str) -> bool:
    explicit = _env_bool_value(value)
    if explicit is not None:
        return explicit
    name = str(model_name or "").lower()
    backend = str(backend_type or "").lower()
    if backend == "ollama" and any(marker in name for marker in ["deepseek-r1", "reasoning", "r1:"]):
        return False
    return True


def load_model_catalog(force_reload: bool = False) -> dict:
    """Build a model catalog from models actually configured in .env/environment."""

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


def _build_model_catalog() -> dict:
    models = []
    for item in discover_model_definitions():
        api_key = item.get("api_key_value") or _env_first(item.get("api_key_env"), item.get("shared_api_key_env"))
        base_url = item.get("base_url_value") or _env_first(item.get("base_url_env"), item.get("shared_base_url_env"))
        model_name = item.get("model_name_value") or os.getenv(item.get("model_env") or "") or item.get("default_model_name")
        backend_type = infer_backend_type(
            base_url or "",
            item.get("provider") or "",
            item.get("backend_type") or "",
        )
        is_local = is_local_backend(backend_type)
        configured = bool(base_url and model_name and (api_key or is_local))
        supports_tools = _supports_tools_from_env_or_guess(
            os.getenv(item.get("supports_tools_env", "")),
            model_name or "",
            backend_type,
        )
        if "supports_tools" in item:
            supports_tools = bool(item.get("supports_tools"))
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
                BASE_DIR,
                {
                "id": item["id"],
                "display_name_zh": item["display_name_zh"],
                "provider": item["provider"],
                "api_key_env": item.get("api_key_env") or "",
                "base_url_env": item.get("base_url_env") or "",
                "model_env": item.get("model_env") or "",
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
                "source": item.get("source") or "env",
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
            "只能选择 configured=true 的模型。",
            "代码任务优先 mimo_model。",
            "复杂规划、Skill 创建、多 Agent 汇总优先 deepseek_V4_pro_model。",
            "中文解释、项目探索、文本润色优先 deepseek_V4_flash_model。",
            "如果首选模型未配置，选择同类能力中 configured=true 的替代模型。",
        ],
        "models": models,
    }


def _model_catalog_signature() -> tuple:
    env_file = BASE_DIR / ".env"
    probe_file = BASE_DIR / ".agent_cache" / "model_capabilities.json"
    env_values = dotenv_values(env_file)
    relevant_keys = {
        key
        for key in set(os.environ) | set(env_values)
        if key
        and (
            key.startswith("MODEL_")
            or key.startswith("DEEPSEEK")
            or key.startswith("MIMO")
            or key.startswith("AGENTS_")
        )
    }
    env_snapshot = tuple(sorted((key, os.environ.get(key, env_values.get(key, "") or "")) for key in relevant_keys))
    return (
        _file_signature(env_file),
        _file_signature(probe_file),
        lucode_config_signature(),
        env_snapshot,
    )


def _file_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def compact_model_catalog_for_prompt() -> str:
    catalog = load_model_catalog()
    lines = ["模型图书馆（只能选择 configured=true）："]
    for item in catalog.get("models", []):
        lines.append(
            "- "
            f"{item['id']} | "
            f"configured:{item.get('configured')} | "
            f"name:{item.get('model_name') or '未配置'} | "
            f"backend:{item.get('backend_type')} | "
            f"privacy:{item.get('privacy_level')} | "
            f"tier:{item.get('model_tier')} | "
            f"tools:{item.get('supports_tools')} | "
            f"能力:{','.join(item.get('strengths') or [])} | "
            f"适合:{','.join(item.get('best_for_skills') or []) or '通用'} | "
            f"成本:{item.get('cost_level')} | "
            f"推理:{item.get('reasoning_level')}"
        )
    return "\n".join(lines)


class ModelRegistry:
    """Create model objects by id using the current .env configuration."""

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
        api_key = item.get("api_key_value") or _env_first(item.get("api_key_env"), item.get("shared_api_key_env"))
        base_url = item.get("base_url_value") or _env_first(item.get("base_url_env"), item.get("shared_base_url_env"))
        model_name = item.get("model_name_value") or os.getenv(item.get("model_env") or "") or item.get("default_model_name")
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

    def first_configured(self, preferred: list[str]) -> str:
        policy = PrivacyPolicy.from_env()
        catalog = load_model_catalog()
        model_infos = {item["id"]: item for item in catalog["models"]}
        configured = {
            item["id"]
            for item in catalog["models"]
            if item["configured"] and _model_runtime_available(item) and policy.model_allowed(item)
        }
        for model_id in policy.sort_model_ids(preferred, model_infos):
            if model_id in configured:
                return model_id
        if configured:
            return sorted(configured)[0]
        raise ValueError(f"No configured models allowed by privacy mode: {policy.mode}")


def _model_runtime_available(model_info: dict) -> bool:
    probe = model_info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    if status in {
        "chat_failed",
        "probe_failed",
        "service_unavailable",
        "model_missing",
        "capability_probe_failed",
        "config_incomplete",
    }:
        return False
    return True


def _merge_probe(project_root: Path, model_info: dict) -> dict:
    merged = dict(model_info)
    probe = cached_probe_for_model(project_root, model_info)
    merged["probe"] = probe or {}
    if probe:
        for key in ["supports_tools", "supports_basic_chat", "supports_json_output", "planner_suitable", "execution_suitable"]:
            if key in probe and probe.get(key) is not None:
                merged[key] = probe.get(key)
    return merged


def _env_first(*names: str | None) -> str:
    for name in names:
        if name:
            value = os.getenv(name)
            if value:
                return value
    return ""
