from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from catalog_system.model_catalog import load_model_catalog
from runtime.config.model_config import (
    iter_model_roles,
    model_ids_from_refs,
    model_role_label,
    normalize_model_ref,
    normalize_model_role,
    project_config_path,
    select_role_model_priority,
)
from runtime.config.settings import RuntimeSettings


@dataclass(frozen=True)
class ModelTunerOption:
    index: int
    ref: str
    model_id: str
    label: str
    capability: str


@dataclass(frozen=True)
class ModelTunerRoleState:
    index: int
    role_id: str
    label: str
    primary: str
    fallback: str


@dataclass(frozen=True)
class ModelTunerState:
    workspace_root: Path
    config_path: Path
    selected_role: str
    roles: list[ModelTunerRoleState] = field(default_factory=list)
    options: list[ModelTunerOption] = field(default_factory=list)


@dataclass(frozen=True)
class ModelTunerApplyResult:
    role_id: str
    role_label: str
    refs: list[str]
    model_ids: list[str]
    message: str


@dataclass(frozen=True)
class ModelTunerCommandItem:
    command: str
    display: str
    meta: str


def build_model_tuner_state(
    settings: RuntimeSettings,
    workspace_context=None,
    *,
    selected_role: str = "orchestrator",
    catalog: dict | None = None,
) -> ModelTunerState:
    workspace_root = _workspace_root(workspace_context)
    model_catalog = catalog if catalog is not None else load_model_catalog()
    model_infos = {str(item.get("id") or ""): item for item in model_catalog.get("models", [])}
    normalized_role = normalize_model_role(selected_role)
    roles: list[ModelTunerRoleState] = []
    for index, (role_id, role_info) in enumerate(iter_model_roles(), start=1):
        priority = settings.model_priority_for(role_id)
        roles.append(
            ModelTunerRoleState(
                index=index,
                role_id=role_id,
                label=str(role_info["label"]),
                primary=_priority_label(priority[:1], model_infos) or "未设置",
                fallback=_priority_label(priority[1:], model_infos) or "-",
            )
        )

    options: list[ModelTunerOption] = []
    for item in model_catalog.get("models", []):
        if not item.get("configured"):
            continue
        ref = _model_ref_for_tuner(item)
        if not ref:
            continue
        options.append(
            ModelTunerOption(
                index=len(options) + 1,
                ref=ref,
                model_id=str(item.get("id") or ""),
                label=_model_label_for_tuner(item),
                capability=_model_capability_for_tuner(item),
            )
        )

    return ModelTunerState(
        workspace_root=workspace_root,
        config_path=project_config_path(workspace_root),
        selected_role=normalized_role,
        roles=roles,
        options=options,
    )


def render_model_tuner_snapshot(
    state: ModelTunerState,
    *,
    message: str = "",
    max_options: int = 8,
) -> str:
    selected_label = model_role_label(state.selected_role)
    selected_state = next((role for role in state.roles if role.role_id == state.selected_role), None)
    selected_primary = selected_state.primary if selected_state is not None else "未设置"
    lines = [
        "Lucode 多脑模型调音台",
        f"项目：{state.workspace_root}",
        f"配置：{_relative_config_path(state)}",
        f"当前脑位：{selected_label} | 当前主模型：{selected_primary}",
    ]
    for role in state.roles:
        marker = ">" if role.role_id == state.selected_role else " "
        lines.append(f"{marker} {role.index}. {role.label:<8} {role.primary} / {role.fallback}")

    lines.append("可选模型")
    if not state.options:
        lines.append("- 暂无已配置模型；先用 /connect 添加 Provider 和 API key。")
    else:
        for option in state.options[:max_options]:
            lines.append(f"{option.index}. {option.label}  {option.ref}  {option.capability}")
        if len(state.options) > max_options:
            lines.append(f"... 还有 {len(state.options) - max_options} 个模型，可输入具体 provider/model")

    lines.append("操作：role 1-4 切换脑位；select 1 应用模型；q 退出；回退命令 /models brain 仍可用。")
    if message:
        lines.append(f"状态：{message}")
    return "\n".join(lines)


def resolve_role_selection(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请提供脑位编号或名称。")
    roles = list(iter_model_roles())
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(roles):
            return roles[index - 1][0]
        raise ValueError("脑位编号只能是 1-4。")
    return normalize_model_role(raw)


def resolve_model_selection(value: str, state: ModelTunerState) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请提供模型编号或 provider/model。")
    normalized_index = raw[1:] if raw.lower().startswith("m") and raw[1:].isdigit() else raw
    if normalized_index.isdigit():
        index = int(normalized_index)
        for option in state.options:
            if option.index == index:
                return option.ref
        raise ValueError(f"没有第 {index} 个模型。")
    return normalize_model_ref(raw)


def apply_model_tuner_selection(
    settings: RuntimeSettings,
    workspace_context,
    *,
    role: str,
    refs: list[str] | tuple[str, ...] | str,
) -> ModelTunerApplyResult:
    role_id = normalize_model_role(role)
    normalized_refs = [normalize_model_ref(ref) for ref in _as_list(refs)]
    if not normalized_refs:
        raise ValueError("请至少选择一个模型。")
    select_role_model_priority(
        workspace_root=_workspace_root(workspace_context),
        role=role_id,
        refs=normalized_refs,
    )
    model_ids = model_ids_from_refs(normalized_refs)
    _apply_role_ids_to_settings(settings, role_id, model_ids)
    role_label = model_role_label(role_id)
    return ModelTunerApplyResult(
        role_id=role_id,
        role_label=role_label,
        refs=normalized_refs,
        model_ids=model_ids,
        message=f"已切换{role_label}：{', '.join(normalized_refs)}，配置已写入 .lucode/config.toml。",
    )


def model_tuner_help() -> str:
    return "用 role 1-4 切换脑位，用 select 1 应用模型，用 q 退出；选择会立即保存。"


def model_tuner_command_items(state: ModelTunerState, *, max_options: int = 12) -> list[ModelTunerCommandItem]:
    items = [
        ModelTunerCommandItem("q", "退出调音台", "返回聊天，不改动模型配置"),
        ModelTunerCommandItem("refresh", "刷新模型列表", "重新读取 Provider 和项目配置"),
    ]
    for role in state.roles:
        prefix = "当前" if role.role_id == state.selected_role else "切换"
        items.append(
            ModelTunerCommandItem(
                f"role {role.index}",
                f"{prefix}脑位：{role.label}",
                f"主模型：{role.primary}",
            )
        )
    for option in state.options[:max_options]:
        items.append(
            ModelTunerCommandItem(
                f"select {option.index}",
                f"应用模型：{option.label}",
                f"{option.ref}  {option.capability}",
            )
        )
    return items


def _workspace_root(workspace_context=None) -> Path:
    value = getattr(workspace_context, "workspace_root", None)
    if value is not None:
        return Path(value).resolve()
    return Path.cwd().resolve()


def _relative_config_path(state: ModelTunerState) -> str:
    try:
        return str(state.config_path.relative_to(state.workspace_root))
    except ValueError:
        return str(state.config_path)


def _priority_label(model_ids: list[str], model_infos: dict[str, dict]) -> str:
    labels = [_model_label_for_tuner(model_infos.get(model_id) or {"id": model_id}) for model_id in model_ids]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} +{len(labels) - 1} 备用"


def _model_ref_for_tuner(model_info: dict) -> str:
    provider_ref = str(model_info.get("provider_ref") or "").strip()
    if provider_ref:
        return provider_ref
    provider = str(model_info.get("provider") or "").strip()
    model_name = str(model_info.get("model_name") or model_info.get("model_name_value") or "").strip()
    if provider and model_name:
        return f"{provider}/{model_name}"
    return str(model_info.get("id") or "").strip()


def _model_label_for_tuner(model_info: dict) -> str:
    return str(
        model_info.get("display_name_zh")
        or model_info.get("display_name")
        or model_info.get("provider_ref")
        or model_info.get("id")
        or "未知模型"
    )


def _model_capability_for_tuner(model_info: dict) -> str:
    flags = []
    if model_info.get("supports_tools") is True:
        flags.append("工具")
    if model_info.get("planner_suitable") is True:
        flags.append("规划")
    if model_info.get("execution_suitable") is True:
        flags.append("执行")
    if model_info.get("is_local"):
        flags.append("本地")
    if not flags:
        flags.append("未探测")
    return " / ".join(flags)


def _apply_role_ids_to_settings(settings: RuntimeSettings, role_id: str, model_ids: list[str]) -> None:
    if role_id == "query_refiner":
        settings.query_refiner_model_priority = list(model_ids)
    elif role_id == "orchestrator":
        settings.orchestrator_model_priority = list(model_ids)
    elif role_id == "executor":
        settings.executor_model_priority = list(model_ids)
    elif role_id == "final_synthesizer":
        settings.final_synthesizer_model_priority = list(model_ids)


def _as_list(value: list[str] | tuple[str, ...] | str) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]
