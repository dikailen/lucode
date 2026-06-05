from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from runtime.safety.privacy import infer_backend_type


ENV_COMPAT_DISABLE_FLAGS = {"1", "true", "yes", "on", "disable", "disabled"}

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


def env_compat_enabled() -> bool:
    dotenv_disabled = str(os.environ.get("LUCODE_DISABLE_DOTENV") or "").strip().lower()
    if dotenv_disabled in ENV_COMPAT_DISABLE_FLAGS:
        return False
    value = str(os.environ.get("LUCODE_DISABLE_ENV_COMPAT") or "").strip().lower()
    return value not in ENV_COMPAT_DISABLE_FLAGS


def discover_env_model_definitions(base_dir: Path) -> list[dict[str, Any]]:
    """Return legacy .env/environment model definitions without mutating os.environ."""

    if not env_compat_enabled():
        return []

    env = _merged_env(base_dir)
    definitions: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    env_keys = {key for key in env if key}

    for item in KNOWN_MODEL_DEFINITIONS:
        if _known_model_is_registered(item, env, env_keys):
            definition = _with_resolved_env_values(dict(item), env)
            definitions.append(definition)
            known_ids.add(definition["id"])

    for group, config in COMPAT_GROUP_DEFINITIONS.items():
        models_value = _env_get(env, config.get("models_env", ""))
        if models_value:
            for item in _shared_model_definitions_for_group(group, models_value, env, config):
                if item["id"] not in known_ids:
                    definitions.append(item)
                    known_ids.add(item["id"])

    for group in sorted(_model_groups_from_env(env)):
        if group in COMPAT_GROUP_DEFINITIONS:
            continue
        shared_models = _env_get(env, f"MODEL_{group}_MODELS")
        if shared_models.strip():
            for item in _shared_model_definitions_for_group(group, shared_models, env):
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

        display_name = _env_get(env, f"MODEL_{group}_DISPLAY_NAME") or group.replace("_", " ").title()
        provider = _env_get(env, f"MODEL_{group}_PROVIDER") or group.lower()
        strengths = _split_env_list(_env_get(env, f"MODEL_{group}_STRENGTHS")) or ["通用任务"]
        best_for_skills = _split_env_list(_env_get(env, f"MODEL_{group}_BEST_FOR_SKILLS"))
        base_url = _env_get(env, f"MODEL_{group}_BASE_URL")
        model_name = _env_get(env, model_env)
        backend_type = infer_backend_type(base_url, provider, _env_get(env, f"MODEL_{group}_BACKEND"))

        definitions.append(
            {
                "id": model_id,
                "display_name_zh": display_name,
                "provider": provider,
                "api_key_env": f"MODEL_{group}_API_KEY",
                "base_url_env": f"MODEL_{group}_BASE_URL",
                "model_env": model_env,
                "api_key_value": _env_get(env, f"MODEL_{group}_API_KEY"),
                "base_url_value": base_url,
                "model_name_value": model_name,
                "supports_tools_env": f"MODEL_{group}_SUPPORTS_TOOLS",
                "strengths": strengths,
                "best_for_skills": best_for_skills,
                "cost_level": _env_get(env, f"MODEL_{group}_COST_LEVEL") or "medium",
                "reasoning_level": _env_get(env, f"MODEL_{group}_REASONING_LEVEL") or "medium",
                "backend_type": backend_type,
                "supports_tools": _supports_tools_from_env_or_guess(
                    _env_get(env, f"MODEL_{group}_SUPPORTS_TOOLS"),
                    model_name,
                    backend_type,
                ),
                "source": "env",
            }
        )
        known_ids.add(model_id)

    return definitions


def env_model_signature(base_dir: Path) -> tuple[Any, ...]:
    if not env_compat_enabled():
        return (("env_compat", "disabled"),)

    env_file = base_dir / ".env"
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
    snapshot = tuple(sorted((key, os.environ.get(key, env_values.get(key, "") or "")) for key in relevant_keys))
    return (("env_compat", "enabled"), _file_signature(env_file), snapshot)


def resolve_env_value(base_dir: Path, *names: str | None) -> str:
    if not env_compat_enabled():
        return ""
    env = _merged_env(base_dir)
    for name in names:
        if name:
            value = _env_get(env, name)
            if value:
                return value
    return ""


def _merged_env(base_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {
        str(key): str(value or "")
        for key, value in dotenv_values(base_dir / ".env").items()
        if key
    }
    for key, value in os.environ.items():
        values[str(key)] = str(value)
    return values


def _model_groups_from_env(env: dict[str, str]) -> set[str]:
    groups = set()
    suffixes = sorted(MODEL_GROUP_SUFFIXES, key=len, reverse=True)
    for key in env:
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


def _known_model_is_registered(item: dict[str, Any], env: dict[str, str], env_keys: set[str]) -> bool:
    if item.get("provider") == "deepseek" and _env_get(env, "MODEL_DEEPSEEK_MODELS"):
        return False
    if item.get("provider") == "mimo" and _env_get(env, "MIMO_API_MODELS"):
        return False
    if item.get("model_env") in env_keys:
        return True
    shared_api = item.get("shared_api_key_env")
    shared_base = item.get("shared_base_url_env")
    if shared_api and shared_base and shared_api in env_keys and shared_base in env_keys:
        return bool(item.get("default_model_name"))
    return False


def _with_resolved_env_values(item: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    item["api_key_value"] = _env_first(env, item.get("api_key_env"), item.get("shared_api_key_env"))
    item["base_url_value"] = _env_first(env, item.get("base_url_env"), item.get("shared_base_url_env"))
    item["model_name_value"] = _env_get(env, item.get("model_env", "")) or item.get("default_model_name") or ""
    item["source"] = "env"
    supports_tools_env = item.get("supports_tools_env")
    if supports_tools_env:
        item["supports_tools"] = _supports_tools_from_env_or_guess(
            _env_get(env, supports_tools_env),
            item["model_name_value"],
            str(item.get("backend_type") or ""),
        )
    return item


def _shared_model_definitions_for_group(
    group: str,
    models_value: str,
    env: dict[str, str],
    group_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    group_config = group_config or {}
    provider = _env_get(env, f"MODEL_{group}_PROVIDER") or group_config.get("provider") or group.lower()
    api_key_env = group_config.get("api_key_env") or f"MODEL_{group}_API_KEY"
    base_url_env = group_config.get("base_url_env") or f"MODEL_{group}_BASE_URL"
    base_url = _env_get(env, str(base_url_env))
    backend_type = infer_backend_type(
        base_url,
        provider,
        _env_get(env, f"MODEL_{group}_BACKEND") or group_config.get("backend_type") or "",
    )
    strengths = _split_env_list(_env_get(env, f"MODEL_{group}_STRENGTHS")) or list(group_config.get("strengths") or ["通用任务"])
    best_for_skills = _split_env_list(_env_get(env, f"MODEL_{group}_BEST_FOR_SKILLS")) or list(group_config.get("best_for_skills") or [])
    display_prefix = _env_get(env, f"MODEL_{group}_DISPLAY_PREFIX") or group_config.get("display_prefix") or group.replace("_", " ").title()
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
                "api_key_value": _env_get(env, str(api_key_env)),
                "base_url_value": base_url,
                "model_name_value": model_name,
                "supports_tools_env": f"MODEL_{group}_SUPPORTS_TOOLS",
                "strengths": strengths,
                "best_for_skills": best_for_skills,
                "cost_level": _env_get(env, f"MODEL_{group}_COST_LEVEL") or "medium",
                "reasoning_level": _env_get(env, f"MODEL_{group}_REASONING_LEVEL") or "medium",
                "backend_type": backend_type,
                "supports_tools": _supports_tools_from_env_or_guess(
                    _env_get(env, f"MODEL_{group}_SUPPORTS_TOOLS"),
                    model_name,
                    backend_type,
                ),
                "shared_config_group": group,
                "model_alias": alias,
                "source": "env",
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


def _normalize_model_id(raw_id: str) -> str:
    value = str(raw_id or "").strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value.endswith("_model"):
        value += "_model"
    return value


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


def _env_get(env: dict[str, str], name: str | None) -> str:
    return str(env.get(str(name or ""), "") or "")


def _env_first(env: dict[str, str], *names: str | None) -> str:
    for name in names:
        value = _env_get(env, name)
        if value:
            return value
    return ""


def _file_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))
