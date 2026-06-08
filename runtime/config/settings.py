import os
from dataclasses import dataclass, field

from catalog_system.model_catalog import load_model_catalog
from runtime.config.execution_mode import normalize_execution_mode
from runtime.config.model_config import (
    load_effective_lucode_config,
    model_ids_from_refs,
    model_refs_from_config,
    normalize_model_role,
)
from runtime.config.model_selection import model_runtime_available
from runtime.safety.privacy import normalize_privacy_mode


DEFAULT_QUERY_REFINER_MODELS: list[str] = []
DEFAULT_ORCHESTRATOR_MODELS: list[str] = []
DEFAULT_FINAL_SYNTHESIZER_MODELS: list[str] = []


@dataclass
class RuntimeSettings:
    """Runtime user preferences loaded from environment variables."""

    query_refiner_enabled: bool = False
    query_refiner_model_priority: list[str] = field(default_factory=lambda: list(DEFAULT_QUERY_REFINER_MODELS))
    orchestrator_model_priority: list[str] = field(default_factory=lambda: list(DEFAULT_ORCHESTRATOR_MODELS))
    executor_model_priority: list[str] = field(default_factory=list)
    final_synthesizer_model_priority: list[str] = field(
        default_factory=lambda: list(DEFAULT_FINAL_SYNTHESIZER_MODELS)
    )
    privacy_mode: str = "local_first"
    execution_mode: str = "solo"

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        default_priorities = _dynamic_default_priorities()
        settings = cls(
            query_refiner_enabled=_env_bool("AGENTS_QUERY_REFINER_ENABLED", False),
            query_refiner_model_priority=_env_list(
                "AGENTS_QUERY_REFINER_MODEL_PRIORITY",
                default_priorities["query_refiner"],
            ),
            orchestrator_model_priority=_env_list(
                "AGENTS_ORCHESTRATOR_MODEL_PRIORITY",
                default_priorities["orchestrator"],
            ),
            executor_model_priority=_env_list(
                "AGENTS_EXECUTOR_MODEL_PRIORITY",
                default_priorities["executor"],
            ),
            final_synthesizer_model_priority=_env_list(
                "AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY",
                default_priorities["final_synthesizer"],
            ),
            privacy_mode=normalize_privacy_mode(os.environ.get("AGENTS_PRIVACY_MODE") or "local_first"),
            execution_mode=normalize_execution_mode(os.environ.get("AGENTS_EXECUTION_MODE") or "solo"),
        )
        return _apply_lucode_config_overrides(settings)

    def model_priority_for(self, role: str) -> list[str]:
        try:
            normalized = normalize_model_role(role)
        except ValueError as exc:
            raise KeyError(f"Unknown runtime model role: {role}") from exc
        if normalized == "query_refiner":
            return list(self.query_refiner_model_priority)
        if normalized == "orchestrator":
            return list(self.orchestrator_model_priority)
        if normalized == "executor":
            return list(self.executor_model_priority)
        if normalized == "final_synthesizer":
            return list(self.final_synthesizer_model_priority)
        raise KeyError(f"Unknown runtime model role: {role}")

    def select_model_id(self, model_registry, role: str) -> str:
        return model_registry.first_configured(self.model_priority_for(role))

    def summary_zh(self) -> str:
        refiner = "开启" if self.query_refiner_enabled else "关闭"
        return (
            "运行偏好："
            f"前置优化={refiner}；"
            f"前置优化模型优先级={','.join(self.query_refiner_model_priority)}；"
            f"主脑模型优先级={','.join(self.orchestrator_model_priority)}；"
            f"执行脑模型优先级={','.join(self.executor_model_priority)}；"
            f"汇总脑模型优先级={','.join(self.final_synthesizer_model_priority)}；"
            f"隐私模式={self.privacy_mode}；"
            f"执行模式={self.execution_mode}"
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disable", "disabled"}


def _config_bool(value) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text not in {"0", "false", "no", "off", "disable", "disabled"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return list(default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


def _apply_lucode_config_overrides(settings: RuntimeSettings) -> RuntimeSettings:
    try:
        config = load_effective_lucode_config()
    except Exception:
        return settings

    mode = str(config.get("mode") or "").strip()
    if mode and not str(os.environ.get("AGENTS_EXECUTION_MODE") or "").strip():
        settings.execution_mode = normalize_execution_mode(mode)
    privacy = str(config.get("privacy") or "").strip()
    if privacy and not str(os.environ.get("AGENTS_PRIVACY_MODE") or "").strip():
        settings.privacy_mode = normalize_privacy_mode(privacy)
    query_refiner_enabled = _config_bool(config.get("query_refiner_enabled"))
    query_refiner_section = config.get("query_refiner") if isinstance(config.get("query_refiner"), dict) else {}
    if query_refiner_enabled is None:
        query_refiner_enabled = _config_bool(query_refiner_section.get("enabled"))
    if query_refiner_enabled is not None and not _env_has("AGENTS_QUERY_REFINER_ENABLED"):
        settings.query_refiner_enabled = query_refiner_enabled

    available_ids = _configured_runtime_model_ids()
    default_refs = model_refs_from_config(config)
    default_ids = _filter_priority_ids(model_ids_from_refs(default_refs), available_ids)
    role_config = config.get("roles") or {}
    if isinstance(role_config, dict):
        query_refiner_ids = _filter_priority_ids(model_ids_from_refs(role_config.get("query_refiner") or []), available_ids)
        orchestrator_ids = _filter_priority_ids(model_ids_from_refs(role_config.get("orchestrator") or []), available_ids)
        executor_ids = _filter_priority_ids(model_ids_from_refs(role_config.get("executor") or []), available_ids)
        final_ids = _filter_priority_ids(model_ids_from_refs(role_config.get("final_synthesizer") or []), available_ids)
        if query_refiner_ids and not _env_has("AGENTS_QUERY_REFINER_MODEL_PRIORITY"):
            settings.query_refiner_model_priority = query_refiner_ids
        if orchestrator_ids and not _env_has("AGENTS_ORCHESTRATOR_MODEL_PRIORITY"):
            settings.orchestrator_model_priority = orchestrator_ids
        if executor_ids and not _env_has("AGENTS_EXECUTOR_MODEL_PRIORITY"):
            settings.executor_model_priority = executor_ids
        if final_ids and not _env_has("AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY"):
            settings.final_synthesizer_model_priority = final_ids

    if default_ids:
        if not _env_has("AGENTS_QUERY_REFINER_MODEL_PRIORITY") and (
            not isinstance(role_config, dict) or not role_config.get("query_refiner")
        ):
            settings.query_refiner_model_priority = list(default_ids)
        if not _env_has("AGENTS_ORCHESTRATOR_MODEL_PRIORITY") and (
            not isinstance(role_config, dict) or not role_config.get("orchestrator")
        ):
            settings.orchestrator_model_priority = list(default_ids)
        if not _env_has("AGENTS_EXECUTOR_MODEL_PRIORITY") and (
            not isinstance(role_config, dict) or not role_config.get("executor")
        ):
            settings.executor_model_priority = list(default_ids)
        if not _env_has("AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY") and (
            not isinstance(role_config, dict) or not role_config.get("final_synthesizer")
        ):
            settings.final_synthesizer_model_priority = list(default_ids)

    return settings


def _env_has(name: str) -> bool:
    return bool(str(os.environ.get(name) or "").strip())


def _configured_runtime_model_ids() -> set[str]:
    try:
        models = load_model_catalog().get("models", [])
    except Exception:
        return set()
    return {
        str(item.get("id") or "")
        for item in models
        if item.get("id") and item.get("configured") and _model_runtime_available(item)
    }


def _filter_priority_ids(model_ids: list[str], available_ids: set[str]) -> list[str]:
    if not available_ids:
        return list(model_ids)
    return [model_id for model_id in model_ids if model_id in available_ids]


def _dynamic_default_priorities() -> dict[str, list[str]]:
    try:
        models = load_model_catalog().get("models", [])
    except Exception:
        models = []

    configured = [item for item in models if item.get("configured") and _model_runtime_available(item)]
    return {
        "query_refiner": _priority_for_role(configured, "query_refiner"),
        "orchestrator": _priority_for_role(configured, "orchestrator"),
        "executor": _priority_for_role(configured, "executor"),
        "final_synthesizer": _priority_for_role(configured, "final_synthesizer"),
    }


def _priority_for_role(models: list[dict], role: str) -> list[str]:
    if not models:
        return []

    eligible = _eligible_models_for_role(models, role)
    if not eligible:
        eligible = list(models)

    ranked = sorted(
        enumerate(eligible),
        key=lambda pair: (
            -_role_score(pair[1], role),
            pair[0],
        ),
    )
    return [item.get("id") for _, item in ranked if item.get("id")]


def _eligible_models_for_role(models: list[dict], role: str) -> list[dict]:
    if role == "query_refiner":
        candidates = [item for item in models if item.get("supports_basic_chat") is not False]
    elif role == "orchestrator":
        candidates = [item for item in models if item.get("planner_suitable") is not False]
    elif role == "executor":
        candidates = [item for item in models if item.get("supports_tools") is not False]
    elif role == "final_synthesizer":
        candidates = [item for item in models if item.get("execution_suitable") is not False]
    else:
        candidates = list(models)
    return candidates or list(models)


def _role_score(model_info: dict, role: str) -> int:
    best_for = set(model_info.get("best_for_skills") or [])
    reasoning = str(model_info.get("reasoning_level") or "").lower()
    cost = str(model_info.get("cost_level") or "").lower()
    tier = str(model_info.get("model_tier") or "").lower()
    score = 0

    if model_info.get("is_local"):
        score += 1
    if model_info.get("supports_tools"):
        score += 2
    if model_info.get("supports_json_output") is True:
        score += 3
    if model_info.get("planner_suitable") is True:
        score += 4
    if model_info.get("execution_suitable") is True:
        score += 2

    score += {"high": 6, "medium": 3, "low": 1}.get(reasoning, 0)
    score += {"large": 5, "medium": 3, "small": 1}.get(tier, 0)

    if role == "query_refiner":
        if best_for.intersection({"project_explorer"}):
            score += 5
        score += {"low": 4, "medium": 2, "high": 0}.get(cost, 1)
    elif role == "orchestrator":
        if best_for.intersection({"orchestrator_planner", "skill_creator"}):
            score += 8
        if reasoning == "high":
            score += 5
    elif role == "executor":
        if "code_engineer" in best_for:
            score += 8
        if model_info.get("execution_suitable") is True:
            score += 6
        if model_info.get("supports_tools") is True:
            score += 4
        if model_info.get("supports_json_output") is True:
            score += 2
        score += {"low": 5, "medium": 3, "high": 1}.get(cost, 1)
        score += {"large": 3, "medium": 4, "small": 2}.get(tier, 1)
    elif role == "final_synthesizer":
        if "final_synthesizer" in best_for:
            score += 8
        if reasoning == "high":
            score += 4
        if best_for.intersection({"orchestrator_planner", "skill_creator"}):
            score += 2

    return score


def _model_runtime_available(model_info: dict) -> bool:
    return model_runtime_available(model_info)
