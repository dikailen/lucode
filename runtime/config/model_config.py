from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback when tomli is installed.
    import tomli as tomllib  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROVIDER_CATALOG_PATH = PROJECT_ROOT / "catalogs" / "provider_catalog.json"

SENSITIVE_PROVIDER_KEYS = {"api_key", "token", "secret", "authorization", "password"}

MODEL_ROLES = {
    "query_refiner": {
        "label": "前置优化脑",
        "aliases": ("refiner", "query_refiner", "前置优化", "前置优化脑", "前置优化副脑"),
    },
    "orchestrator": {
        "label": "主脑规划脑",
        "aliases": ("planner", "main", "main_brain", "orchestrator", "主脑", "主脑规划", "主脑规划脑"),
    },
    "executor": {
        "label": "执行专家脑",
        "aliases": ("executor", "execution", "worker", "agent", "specialist", "solo", "执行", "执行脑", "执行专家", "执行专家脑", "专家脑", "solo_agent"),
    },
    "final_synthesizer": {
        "label": "汇总脑",
        "aliases": ("synthesizer", "final", "final_brain", "final_synthesizer", "汇总", "汇总脑", "汇总副脑"),
    },
}

ROLE_ORDER = ("query_refiner", "orchestrator", "executor", "final_synthesizer")


def load_provider_catalog(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    catalog_path = Path(path) if path is not None else DEFAULT_PROVIDER_CATALOG_PATH
    if not catalog_path.exists():
        return {}
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog: dict[str, dict[str, Any]] = {}
    for raw_id, raw_item in data.items():
        provider_id = normalize_provider_id(str(raw_id))
        item = dict(raw_item or {})
        item["id"] = provider_id
        item.setdefault("display_name", provider_id)
        item.setdefault("homepage", "")
        item.setdefault("base_url", "")
        item.setdefault("compatible_type", "openai_compatible")
        item.setdefault("models", [])
        catalog[provider_id] = item
    return catalog


def normalize_provider_id(value: str) -> str:
    provider_id = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower())
    provider_id = re.sub(r"_+", "_", provider_id).strip("_")
    if not provider_id:
        raise ValueError("Provider ID 不能为空。")
    return provider_id


def user_home_path(user_home: Path | str | None = None) -> Path:
    if user_home is not None:
        return Path(user_home).expanduser().resolve()
    env_value = os.environ.get("LUCODE_USER_HOME")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (Path.home() / ".lucode").resolve()


def workspace_root_path(workspace_root: Path | str | None = None) -> Path:
    if workspace_root is not None:
        return Path(workspace_root).expanduser().resolve()
    env_value = os.environ.get("LUCODE_WORKSPACE_ROOT")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path.cwd().resolve()


def auth_path(user_home: Path | str | None = None) -> Path:
    return user_home_path(user_home) / "auth.json"


def project_config_path(workspace_root: Path | str | None = None) -> Path:
    return workspace_root_path(workspace_root) / ".lucode" / "config.toml"


def user_config_path(user_home: Path | str | None = None) -> Path:
    return user_home_path(user_home) / "config.toml"


def load_auth(user_home: Path | str | None = None) -> dict[str, Any]:
    path = auth_path(user_home)
    if not path.exists():
        return {"providers": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"auth.json 格式不正确：{path}")
    providers = data.get("providers")
    if not isinstance(providers, dict):
        data["providers"] = {}
    return data


def save_auth(data: dict[str, Any], user_home: Path | str | None = None) -> Path:
    path = auth_path(user_home)
    payload = deepcopy(data)
    payload.setdefault("providers", {})
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def provider_has_api_key(provider_id: str, user_home: Path | str | None = None) -> bool:
    provider_id = normalize_provider_id(provider_id)
    provider_auth = (load_auth(user_home).get("providers") or {}).get(provider_id) or {}
    return bool(str(provider_auth.get("api_key") or "").strip())


def connect_provider(
    provider_id: str,
    *,
    api_key: str | None = None,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
    homepage: str | None = None,
    base_url: str | None = None,
    models: list[str] | tuple[str, ...] | str | None = None,
    display_name: str | None = None,
    compatible_type: str | None = None,
    local: bool | None = None,
    supports_tools: bool | str | None = None,
    custom: bool = False,
) -> dict[str, Any]:
    provider_id = normalize_provider_id(provider_id)
    catalog = load_provider_catalog()
    preset = {} if custom else dict(catalog.get(provider_id) or {})
    if not preset and provider_id == "custom_openai_compatible":
        preset = dict(catalog.get(provider_id) or {})

    resolved_homepage = (homepage or preset.get("homepage") or "").strip()
    resolved_base_url = (base_url or preset.get("base_url") or "").strip()
    if not resolved_homepage:
        raise ValueError("自定义或连接 Provider 时必须提供 homepage（官网/控制台地址）。")
    if not resolved_base_url:
        raise ValueError("自定义或连接 Provider 时必须提供 base_url（真实模型请求地址）。")

    resolved_local = bool(preset.get("local", False) if local is None else local)
    resolved_models = _as_string_list(models if models is not None else preset.get("models") or [])
    if custom:
        if not resolved_models:
            raise ValueError("自定义中转必须至少提供一个模型名，例如 --model qwen-max。")
        if not resolved_local and not str(api_key or "").strip():
            raise ValueError("自定义中转必须提供 API key；密钥只会保存到用户级 auth.json。")
    provider_config: dict[str, Any] = {
        "display_name": display_name or preset.get("display_name") or provider_id,
        "homepage": resolved_homepage,
        "base_url": resolved_base_url,
        "compatible_type": compatible_type or preset.get("compatible_type") or "openai_compatible",
        "models": resolved_models,
    }
    if resolved_local:
        provider_config["local"] = True
    resolved_supports_tools = supports_tools if supports_tools is not None else preset.get("supports_tools")
    if resolved_supports_tools is not None:
        provider_config["supports_tools"] = resolved_supports_tools
    for optional_key in ["reasoning_level", "cost_level", "strengths", "best_for_skills"]:
        if preset.get(optional_key) is not None:
            provider_config[optional_key] = preset[optional_key]

    config = load_lucode_config(workspace_root=workspace_root)
    providers = dict(config.get("provider") or {})
    providers[provider_id] = _sanitize_provider_config(provider_config)
    config["provider"] = providers
    save_lucode_config(config, workspace_root=workspace_root)

    if api_key and not resolved_local:
        auth = load_auth(user_home=user_home)
        auth_providers = dict(auth.get("providers") or {})
        auth_providers[provider_id] = {"api_key": str(api_key)}
        auth["providers"] = auth_providers
        save_auth(auth, user_home=user_home)

    return {"provider_id": provider_id, "provider": provider_config}


def remove_provider_auth(provider_id: str, user_home: Path | str | None = None) -> bool:
    provider_id = normalize_provider_id(provider_id)
    auth = load_auth(user_home=user_home)
    providers = dict(auth.get("providers") or {})
    existed = provider_id in providers
    providers.pop(provider_id, None)
    auth["providers"] = providers
    save_auth(auth, user_home=user_home)
    return existed


def remove_provider_config(
    provider_id: str,
    *,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
    remove_auth: bool = True,
) -> dict[str, Any]:
    provider_id = normalize_provider_id(provider_id)
    config = load_lucode_config(workspace_root=workspace_root)
    providers = dict(config.get("provider") or {})
    provider_removed = provider_id in providers
    providers.pop(provider_id, None)
    if providers:
        config["provider"] = providers
    else:
        config.pop("provider", None)

    cleanup = prune_model_refs_from_config(config, removed_provider=provider_id)
    if provider_removed or cleanup["changed"]:
        save_lucode_config(config, workspace_root=workspace_root)

    auth_removed = remove_provider_auth(provider_id, user_home=user_home) if remove_auth else False
    return {
        "provider_id": provider_id,
        "provider_removed": provider_removed,
        "auth_removed": auth_removed,
        **cleanup,
    }


def load_lucode_config(
    *,
    workspace_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else project_config_path(workspace_root)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def save_lucode_config(
    config: dict[str, Any],
    *,
    workspace_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> Path:
    path = Path(config_path) if config_path is not None else project_config_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _dump_lucode_toml(config))
    return path


def load_effective_lucode_config(
    *,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
) -> dict[str, Any]:
    user_config = load_lucode_config(config_path=user_config_path(user_home))
    project_config = load_lucode_config(workspace_root=workspace_root)
    return _deep_merge(user_config, project_config)


def select_model_priority(
    *,
    workspace_root: Path | str | None = None,
    primary_ref: str,
    fallback_refs: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, Any]:
    primary = normalize_model_ref(primary_ref)
    fallback = [normalize_model_ref(item) for item in _as_string_list(fallback_refs or []) if item.strip()]
    config = load_lucode_config(workspace_root=workspace_root)
    model_config = dict(config.get("model") or {})
    model_config["primary"] = primary
    model_config["fallback"] = fallback
    config["model"] = model_config
    save_lucode_config(config, workspace_root=workspace_root)
    return model_config


def select_role_model_priority(
    *,
    workspace_root: Path | str | None = None,
    role: str,
    refs: list[str] | tuple[str, ...] | str,
) -> dict[str, Any]:
    role_id = normalize_model_role(role)
    normalized_refs = [normalize_model_ref(item) for item in _as_string_list(refs) if str(item).strip()]
    if not normalized_refs:
        raise ValueError("请至少提供一个模型引用，例如 deepseek/deepseek-chat。")
    config = load_lucode_config(workspace_root=workspace_root)
    roles = dict(config.get("roles") or {})
    roles[role_id] = normalized_refs
    config["roles"] = roles
    save_lucode_config(config, workspace_root=workspace_root)
    return roles


def reset_role_model_priorities(*, workspace_root: Path | str | None = None) -> dict[str, Any]:
    config = load_lucode_config(workspace_root=workspace_root)
    config.pop("roles", None)
    save_lucode_config(config, workspace_root=workspace_root)
    return config


def prune_model_refs_from_config(
    config: dict[str, Any],
    *,
    removed_provider: str | None = None,
    valid_refs: set[str] | None = None,
) -> dict[str, Any]:
    before = deepcopy({"model": config.get("model"), "roles": config.get("roles")})
    provider_id = normalize_provider_id(removed_provider) if removed_provider else ""
    valid_normalized = {normalize_model_ref(item) for item in valid_refs} if valid_refs is not None else None
    removed_count = 0
    removed_roles: list[str] = []

    model_config = config.get("model")
    if isinstance(model_config, dict):
        model_refs = []
        primary = str(model_config.get("primary") or "").strip()
        if primary:
            model_refs.append(primary)
        model_refs.extend(_as_string_list(model_config.get("fallback") or []))
        cleaned_refs, removed = _clean_model_refs(
            model_refs,
            removed_provider=provider_id,
            valid_refs=valid_normalized,
        )
        removed_count += removed
        extra_model_config = {key: value for key, value in model_config.items() if key not in {"primary", "fallback"}}
        if cleaned_refs:
            config["model"] = {
                **extra_model_config,
                "primary": cleaned_refs[0],
                "fallback": cleaned_refs[1:],
            }
        elif extra_model_config:
            config["model"] = extra_model_config
        else:
            config.pop("model", None)

    roles_config = config.get("roles")
    if isinstance(roles_config, dict):
        cleaned_roles: dict[str, list[str]] = {}
        for raw_role, refs_value in roles_config.items():
            try:
                role_id = normalize_model_role(str(raw_role))
            except ValueError:
                role_id = str(raw_role or "").strip()
            cleaned_refs, removed = _clean_model_refs(
                refs_value,
                removed_provider=provider_id,
                valid_refs=valid_normalized,
            )
            removed_count += removed
            if cleaned_refs and role_id:
                merged = cleaned_roles.setdefault(role_id, [])
                for ref in cleaned_refs:
                    if ref not in merged:
                        merged.append(ref)
            elif role_id:
                removed_roles.append(role_id)
        if cleaned_roles:
            config["roles"] = cleaned_roles
        else:
            config.pop("roles", None)

    after = {"model": config.get("model"), "roles": config.get("roles")}
    return {
        "changed": before != after,
        "removed_model_refs": removed_count,
        "removed_roles": sorted(set(removed_roles)),
    }


def normalize_model_role(role: str) -> str:
    value = str(role or "").strip().lower().replace("-", "_")
    for canonical, info in MODEL_ROLES.items():
        if value in info["aliases"] or value == canonical:
            return canonical
    labels = "、".join(info["label"] for info in MODEL_ROLES.values())
    raise ValueError(f"未知角色。可用角色：{labels}。也可用英文别名：{', '.join(MODEL_ROLES)}。")


def model_role_label(role: str) -> str:
    canonical = normalize_model_role(role)
    return MODEL_ROLES[canonical]["label"]


def iter_model_roles():
    for role_id in ROLE_ORDER:
        yield role_id, MODEL_ROLES[role_id]


def configured_provider_model_definitions(
    *,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
) -> list[dict[str, Any]]:
    config = load_effective_lucode_config(workspace_root=workspace_root, user_home=user_home)
    auth = load_auth(user_home=user_home)
    auth_providers = auth.get("providers") or {}
    definitions: list[dict[str, Any]] = []

    for provider_id, provider_config in sorted((config.get("provider") or {}).items()):
        provider_id = normalize_provider_id(provider_id)
        if not isinstance(provider_config, dict):
            continue
        models = _as_string_list(provider_config.get("models") or [])
        if not models:
            continue
        api_key = str((auth_providers.get(provider_id) or {}).get("api_key") or "")
        base_url = str(provider_config.get("base_url") or "").strip()
        local = bool(provider_config.get("local"))
        compatible_type = str(provider_config.get("compatible_type") or provider_config.get("backend_type") or "openai_compatible")
        display_name = str(provider_config.get("display_name") or provider_id)
        supports_tools = provider_config.get("supports_tools")

        for model_name in models:
            model_name = str(model_name).strip()
            if not model_name:
                continue
            item: dict[str, Any] = {
                "id": model_id_for_provider_model(provider_id, model_name),
                "display_name_zh": f"{display_name} {model_name}",
                "provider": provider_id,
                "api_key_env": "",
                "base_url_env": "",
                "model_env": "",
                "api_key_value": api_key,
                "base_url_value": base_url,
                "model_name_value": model_name,
                "strengths": _as_string_list(provider_config.get("strengths") or ["通用任务"]),
                "best_for_skills": _as_string_list(provider_config.get("best_for_skills") or []),
                "cost_level": str(provider_config.get("cost_level") or ("local" if local else "medium")),
                "reasoning_level": str(provider_config.get("reasoning_level") or "medium"),
                "backend_type": "ollama" if compatible_type == "ollama" else compatible_type,
                "source": "lucode_config",
                "provider_ref": f"{provider_id}/{model_name}",
                "homepage": str(provider_config.get("homepage") or ""),
            }
            if isinstance(supports_tools, bool):
                item["supports_tools"] = supports_tools
            definitions.append(item)

    return definitions


def model_refs_from_config(config: dict[str, Any]) -> list[str]:
    model_config = config.get("model") or {}
    refs = []
    primary = str(model_config.get("primary") or "").strip()
    if primary:
        refs.append(primary)
    refs.extend(_as_string_list(model_config.get("fallback") or []))
    return [normalize_model_ref(item) for item in refs if str(item).strip()]


def model_ids_from_refs(refs: list[str] | tuple[str, ...] | str | None) -> list[str]:
    ids = []
    for ref in _as_string_list(refs or []):
        model_id = model_id_for_ref(ref)
        if model_id and model_id not in ids:
            ids.append(model_id)
    return ids


def model_id_for_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if not value:
        return ""
    if "/" not in value:
        return _normalize_model_id(value)
    provider_id, model_name = value.split("/", 1)
    return model_id_for_provider_model(provider_id, model_name)


def normalize_model_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if not value:
        raise ValueError("模型引用不能为空。")
    if "/" not in value:
        return value
    provider_id, model_name = value.split("/", 1)
    provider_id = normalize_provider_id(provider_id)
    model_name = model_name.strip()
    if not model_name:
        raise ValueError("模型引用必须包含模型名，例如 deepseek/deepseek-chat。")
    return f"{provider_id}/{model_name}"


def model_id_for_provider_model(provider_id: str, model_name: str) -> str:
    provider_alias = _model_alias_from_name(normalize_provider_id(provider_id))
    model_alias = _model_alias_from_name(model_name)
    if model_alias == provider_alias or model_alias.startswith(f"{provider_alias}_"):
        return _normalize_model_id(model_alias)
    return _normalize_model_id(f"{provider_alias}_{model_alias}")


def _clean_model_refs(
    refs: list[str] | tuple[str, ...] | str | None,
    *,
    removed_provider: str = "",
    valid_refs: set[str] | None = None,
) -> tuple[list[str], int]:
    cleaned: list[str] = []
    removed = 0
    for raw_ref in _as_string_list(refs or []):
        if not str(raw_ref).strip():
            continue
        try:
            ref = normalize_model_ref(raw_ref)
        except ValueError:
            removed += 1
            continue
        if removed_provider and "/" in ref and ref.split("/", 1)[0] == removed_provider:
            removed += 1
            continue
        if valid_refs is not None and ref not in valid_refs:
            removed += 1
            continue
        if ref not in cleaned:
            cleaned.append(ref)
    return cleaned, removed


def lucode_config_signature(
    *,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
) -> tuple[Any, ...]:
    workspace = workspace_root_path(workspace_root)
    user = user_home_path(user_home)
    return (
        ("workspace", str(workspace)),
        ("user_home", str(user)),
        _file_signature(project_config_path(workspace)),
        _file_signature(user_config_path(user)),
        _file_signature(auth_path(user)),
        _file_signature(DEFAULT_PROVIDER_CATALOG_PATH),
    )


def _sanitize_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = {}
    for key, value in config.items():
        if key.lower() in SENSITIVE_PROVIDER_KEYS:
            continue
        sanitized[key] = value
    return sanitized


def _dump_lucode_toml(config: dict[str, Any]) -> str:
    lines: list[str] = []
    root_items = {
        key: value
        for key, value in config.items()
        if key not in {"model", "roles", "ui", "provider"} and not isinstance(value, dict)
    }
    for key in sorted(root_items):
        lines.append(f"{key} = {_toml_value(root_items[key])}")
    if root_items:
        lines.append("")

    for section in ["model", "roles", "ui"]:
        mapping = config.get(section)
        if isinstance(mapping, dict) and mapping:
            lines.append(f"[{section}]")
            for key in sorted(mapping):
                lines.append(f"{key} = {_toml_value(mapping[key])}")
            lines.append("")

    providers = config.get("provider") or {}
    if isinstance(providers, dict):
        for provider_id in sorted(providers):
            provider_config = providers.get(provider_id)
            if not isinstance(provider_config, dict):
                continue
            lines.append(f"[provider.{normalize_provider_id(provider_id)}]")
            for key in sorted(provider_config):
                if key.lower() in SENSITIVE_PROVIDER_KEYS:
                    continue
                lines.append(f"{key} = {_toml_value(provider_config[key])}")
            lines.append("")

    text = "\n".join(lines).rstrip()
    return f"{text}\n" if text else ""


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _as_string_list(value: list[str] | tuple[str, ...] | str | Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]
    return [str(item).strip() for item in raw_items if str(item).strip()]


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


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        Path(tmp_name).replace(path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _file_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))
