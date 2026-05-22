from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from catalog_system.model_catalog import load_model_catalog
from runtime.config.connect_command import (
    apply_provider_connect_request,
    parse_slash_connect_command,
    redact_connect_secret,
    render_provider_connect_success,
)
from runtime.config.execution_mode import execution_mode_label_zh
from runtime.config.model_config import (
    auth_path,
    load_auth,
    load_effective_lucode_config,
    load_provider_catalog,
    model_ids_from_refs,
    model_refs_from_config,
    model_role_label,
    iter_model_roles,
    normalize_provider_id,
    normalize_model_role,
    project_config_path,
    provider_has_api_key,
    remove_provider_config,
    reset_role_model_priorities,
    select_role_model_priority,
    select_model_priority,
)
from runtime.config.model_tuner import build_model_tuner_state, render_model_tuner_snapshot
from runtime.config.extensions import (
    render_all_mcp,
    render_all_skills,
    render_workspace_mcp,
    render_workspace_skills,
)
from runtime.safety.permissions import render_permission_policy
from runtime.safety.privacy import PrivacyPolicy
from runtime.hooks import render_tool_event_audit
from runtime.config.settings import RuntimeSettings
from runtime.commands.registry import known_command_prefixes
from runtime.tools.registry import render_tool_registry
from runtime.ui.command_palette import render_command_palette

LUCODE_BLUE = "\033[94m"
LUCODE_CYAN = "\033[96m"
LUCODE_DIM = "\033[90m"
ANSI_RESET = "\033[0m"
PANEL_WIDTH = 96


def render_readonly_command(command: str, settings: RuntimeSettings, workspace_context=None) -> str:
    normalized = (command or "").strip()
    lower = normalized.lower()

    if lower == "/config":
        return _render_config(settings)
    if lower in {"/", "/help", "/?"}:
        return render_command_palette(workspace_context=workspace_context)
    if lower.startswith("/help "):
        return render_command_palette(normalized.split(maxsplit=1)[1], workspace_context=workspace_context)
    if lower in {"/api", "/refiner"}:
        return render_command_palette(normalized, workspace_context=workspace_context)
    if lower.startswith("/") and len(lower) > 1 and not lower.startswith(("/plan", "/diff", "/rollback", "/new", "/stop", "/exit")):
        menu = render_command_palette(normalized, workspace_context=workspace_context)
        if "没有匹配命令" not in menu and lower.split()[0] not in _KNOWN_COMMAND_PREFIXES:
            return menu
    if lower == "/api show":
        return _render_api_show(settings)
    if lower == "/privacy":
        return _render_privacy(settings)
    if lower == "/mode":
        return _render_mode(settings)
    if lower == "/model":
        return _render_model(settings)
    if lower in {"/model available", "/models available"}:
        return _render_model_available(settings)
    if lower in {"/connect", "/connect list"}:
        return _render_connect(workspace_context)
    if lower in {"/models", "/model", "/model select"}:
        return _render_model_brains(settings, workspace_context)
    if lower in {"/models brain", "/model brain"}:
        return _render_model_brains(settings, workspace_context)
    if lower in {"/models roles", "/model roles"}:
        return _render_model_roles(settings, workspace_context)
    if lower in {"/models list", "/model list"}:
        return _render_models(settings, workspace_context)
    if lower == "/skills":
        return render_workspace_skills(workspace_context)
    if lower == "/skills_all":
        return render_all_skills(workspace_context)
    if lower == "/mcp":
        return render_workspace_mcp(workspace_context)
    if lower == "/mcp_all":
        return render_all_mcp(workspace_context)
    if lower == "/tools":
        return render_tool_registry(settings, workspace_context, include_all=False)
    if lower == "/tools_all":
        return render_tool_registry(settings, workspace_context, include_all=True)
    if lower == "/permissions":
        return render_permission_policy(_workspace_root(workspace_context))
    if lower in {"/audit", "/hooks"}:
        return render_tool_event_audit(_workspace_root(workspace_context))
    remove_match = re.match(r"^/connect\s+(?:remove|delete|rm|logout)\s+([^\s]+)$", normalized, flags=re.IGNORECASE)
    if remove_match:
        provider_id = remove_match.group(1).strip()
        return "\n".join(
            [
                f"删除 Provider：{provider_id}",
                f"执行命令：/connect remove {provider_id}",
                "会删除项目级 Provider 配置和用户级 API key，并清理 /models 与四脑脑位里的失效模型引用。",
            ]
        )
    if lower.startswith("/connect ") and "--" in lower:
        request = None
        try:
            request = parse_slash_connect_command(normalized)
        except Exception:
            pass
        return "\n".join(
            [
                "连接命令包含写入参数。",
                "请直接执行这条 /connect 命令完成写入；只读预览不会显示 API key。",
                "API key 会保存到用户级 auth.json，不会写入项目配置。",
                redact_connect_secret("命令：/connect <provider> --api-key <key>", request),
            ]
        )
    if lower.startswith("/connect "):
        return _render_connect_provider_hint(normalized, workspace_context)
    if lower.startswith("/models ") and not lower.startswith("/models select "):
        return _render_readonly_switch_hint("/models", normalized.split(maxsplit=1)[1])
    if lower.startswith("/privacy "):
        return _render_readonly_switch_hint("/privacy", normalized.split(maxsplit=1)[1])
    if lower.startswith("/mode "):
        return _render_readonly_switch_hint("/mode", normalized.split(maxsplit=1)[1])
    if lower.startswith("/model ") and lower not in {"/model available", "/model select"}:
        return _render_readonly_switch_hint("/model", normalized.split(maxsplit=1)[1])
    if lower.startswith("/api "):
        return _render_readonly_switch_hint("/api", normalized.split(maxsplit=1)[1])
    return ""


def parse_writable_config_command(command: str) -> tuple[str, str] | None:
    normalized = (command or "").strip()
    probe_match = re.match(r"^/(?:models|model)\s+probe(?:\s+(.*))?$", normalized, flags=re.IGNORECASE)
    if probe_match:
        return ("models_probe", (probe_match.group(1) or "").strip())
    remove_match = re.match(r"^/connect\s+(?:remove|delete|rm|logout)\s+([^\s]+)$", normalized, flags=re.IGNORECASE)
    if remove_match:
        return ("connect_remove", remove_match.group(1).strip())
    if normalized.lower().startswith("/connect ") and "--" in normalized:
        return ("connect", normalized)
    if re.match(r"^/(?:models|model)\s+brain\s+reset$", normalized, flags=re.IGNORECASE):
        return ("models_brain_reset", "")
    brain_match = re.match(r"^/(?:models|model)\s+brain\s+([^\s]+)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if brain_match:
        return ("models_brain", f"{brain_match.group(1).strip()} {brain_match.group(2).strip()}")
    role_match = re.match(r"^/(?:models|model)\s+role\s+([^\s]+)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if role_match:
        return ("models_role", f"{role_match.group(1).strip()} {role_match.group(2).strip()}")
    select_match = re.match(r"^/(?:models|model)\s+select\s+(.+)$", normalized, flags=re.IGNORECASE)
    if select_match:
        return ("models_select", select_match.group(1).strip())
    parts = normalized.split()
    if len(parts) != 2:
        return None
    name, value = parts[0].lower(), parts[1].lower()
    if name == "/mode" and value in {"solo", "serial", "full"}:
        return ("mode", value)
    if name == "/refiner" and value in {"on", "off"}:
        return ("refiner", value)
    return None


def apply_writable_config_command(
    command: str,
    env_path: Path,
    settings: RuntimeSettings,
    workspace_context=None,
) -> tuple[str, bool]:
    parsed = parse_writable_config_command(command)
    if parsed is None:
        return (
            "无法识别这个配置切换命令。\n"
            "可用命令：/mode solo、/mode serial、/mode full、/refiner on、/refiner off、"
            "/models select provider/model [fallback...]、/models role <role> provider/model [...]",
            False,
        )

    kind, value = parsed
    if kind == "models_probe":
        return (_run_models_probe(value, workspace_context), True)

    if kind == "connect":
        request = None
        try:
            request = parse_slash_connect_command(value)
            if request is None:
                raise ValueError("请使用 /connect <provider> --api-key <key>，或输入 /connect <provider> 查看连接方式。")
            result = apply_provider_connect_request(
                request,
                workspace_root=_workspace_root(workspace_context),
                user_home=_user_home(workspace_context),
            )
            try:
                from catalog_system.model_catalog import clear_model_catalog_cache
                from runtime.commands.completion import clear_completion_caches

                clear_model_catalog_cache()
                clear_completion_caches()
            except Exception:
                pass
        except Exception as exc:
            return (f"连接失败：{redact_connect_secret(str(exc), request)}", False)
        return (render_provider_connect_success(result, api_key_provided=bool(request.api_key)), True)

    if kind == "connect_remove":
        try:
            result = remove_provider_config(
                value,
                workspace_root=_workspace_root(workspace_context),
                user_home=_user_home(workspace_context),
            )
            try:
                from catalog_system.model_catalog import clear_model_catalog_cache
                from runtime.commands.completion import clear_completion_caches

                clear_model_catalog_cache()
                clear_completion_caches()
            except Exception:
                pass
            _reload_model_priorities(settings, workspace_context)
        except Exception as exc:
            return (f"删除 Provider 失败：{exc}", False)
        if not result["provider_removed"] and not result["auth_removed"]:
            return (f"没有找到 Provider：{result['provider_id']}，未改动配置。", False)
        role_text = "、".join(result.get("removed_roles") or []) or "无"
        return (
            f"已删除 Provider：{result['provider_id']}\n"
            f"项目配置：{'已删除' if result['provider_removed'] else '未找到'}\n"
            f"API key：{'已删除' if result['auth_removed'] else '未找到'}\n"
            f"已清理失效模型引用：{result.get('removed_model_refs', 0)} 个\n"
            f"已移除空脑位覆盖：{role_text}\n"
            "当前会话已重新加载模型优先级；失效脑位会回到默认可用模型顺序。",
            True,
        )

    if kind == "models_brain_reset":
        try:
            reset_role_model_priorities(workspace_root=_workspace_root(workspace_context))
            _reload_model_priorities(settings, workspace_context)
        except Exception as exc:
            return (f"多脑模型重置失败：{exc}", False)
        return (
            "已重置多脑模型覆盖配置。\n"
            "已删除项目配置中的 [roles]，当前会话已回到默认模型优先级。",
            True,
        )

    if kind == "models_brain":
        try:
            role, refs = _parse_model_role_value(value)
            normalized_role = normalize_model_role(role)
            select_role_model_priority(
                workspace_root=_workspace_root(workspace_context),
                role=role,
                refs=refs,
            )
            model_ids = model_ids_from_refs(refs)
            _apply_role_priority_to_settings(settings, normalized_role, model_ids)
        except Exception as exc:
            return (f"脑位模型切换失败：{exc}", False)
        label = model_role_label(normalized_role)
        refs_text = ", ".join(refs)
        return (
            f"已切换{label}：{refs_text}\n"
            "配置已写入：.lucode/config.toml\n"
            "当前会话已立即生效。",
            True,
        )

    if kind == "models_role":
        try:
            role, refs = _parse_model_role_value(value)
            select_role_model_priority(
                workspace_root=_workspace_root(workspace_context),
                role=role,
                refs=refs,
            )
            model_ids = model_ids_from_refs(refs)
            _apply_role_priority_to_settings(settings, role, model_ids)
        except Exception as exc:
            return (f"模型角色配置失败：{exc}", False)
        return (
            "已更新项目角色模型优先级。\n"
            f"角色：{role}\n"
            f"模型顺序：{', '.join(refs)}\n"
            "配置已写入 .lucode/config.toml，API key 仍只保存在用户级 auth.json。",
            True,
        )

    if kind == "models_select":
        try:
            primary_ref, fallback_refs = _parse_model_select_value(value)
            select_model_priority(
                workspace_root=_workspace_root(workspace_context),
                primary_ref=primary_ref,
                fallback_refs=fallback_refs,
            )
            model_ids = model_ids_from_refs([primary_ref, *fallback_refs])
            settings.query_refiner_model_priority = list(model_ids)
            settings.orchestrator_model_priority = list(model_ids)
            settings.executor_model_priority = list(model_ids)
            settings.final_synthesizer_model_priority = list(model_ids)
        except Exception as exc:
            return (f"模型选择失败：{exc}", False)
        fallback_text = "、".join(fallback_refs) if fallback_refs else "无"
        return (
            "已更新项目模型优先级。\n"
            f"当前主模型：{primary_ref}\n"
            f"Fallback：{fallback_text}\n"
            "配置已写入 .lucode/config.toml，API key 仍只保存在用户级 auth.json。",
            True,
        )

    if kind == "mode":
        _set_env_value(env_path, "AGENTS_EXECUTION_MODE", value)
        os.environ["AGENTS_EXECUTION_MODE"] = value
        settings.execution_mode = value
        return (
            f"已切换执行模式：{execution_mode_label_zh(value)}。\n"
            "本次会话已立即生效，配置也已写入 .env。",
            True,
        )

    enabled = value == "on"
    _set_env_value(env_path, "AGENTS_QUERY_REFINER_ENABLED", "true" if enabled else "false")
    os.environ["AGENTS_QUERY_REFINER_ENABLED"] = "true" if enabled else "false"
    settings.query_refiner_enabled = enabled
    return (
        f"前置优化副脑已{'开启' if enabled else '关闭'}。\n"
        "本次会话已立即生效，配置也已写入 .env。",
        True,
    )


def _run_models_probe(value: str, workspace_context=None) -> str:
    force = str(value or "").strip().lower() in {"force", "--force", "-f", "all", "全部", "刷新"}
    try:
        from catalog_system.model_catalog import clear_model_catalog_cache
        from catalog_system.model_probe import refresh_model_probe_cache

        workspace_root = _workspace_root(workspace_context)
        catalog = load_model_catalog(force_reload=True)
        cache = refresh_model_probe_cache(workspace_root, catalog, force=force, local_only=False)
        clear_model_catalog_cache()
        refreshed = load_model_catalog(force_reload=True)
    except Exception as exc:
        return _render_lucode_panel(
            "模型能力探测",
            [
                "探测失败，但程序没有退出。",
                f"错误：{exc}",
                "建议：检查网络、API key、base_url 和模型名；也可以稍后重试 /models probe force。",
            ],
        )

    results = cache.get("results") or {}
    configured_models = [item for item in refreshed.get("models", []) if item.get("configured")]
    lines = [
        "已完成模型能力探测 v2.3。",
        f"范围：已配置模型 {len(configured_models)} 个；云端和本地都会探测；结果已写入 .agent_cache/model_capabilities.json。",
        "检测项：key / base_url / model name / chat / JSON / tools / stream / latency / context / 推荐脑位。",
        "",
    ]
    for model in configured_models:
        probe = results.get(model.get("id") or "") or model.get("probe") or {}
        lines.append(
            f"- {_format_model_title(model)} | {_format_probe_status({'probe': probe})} | "
            f"chat {_format_bool_zh(probe.get('supports_basic_chat'))} | "
            f"JSON {_format_bool_zh(probe.get('supports_json_output'))} | "
            f"tools {_format_tools_probe_summary(probe)} | "
            f"stream {_format_bool_zh(probe.get('supports_streaming'))} | "
            f"{_format_model_probe_badges(model)}"
        )
        missing = probe.get("missing") or []
        if missing:
            lines.append(f"  缺少：{', '.join(str(item) for item in missing)}")
        error = probe.get("chat_error") or probe.get("tool_error") or probe.get("stream_error") or probe.get("error")
        if error:
            lines.append(f"  提示：{str(error)[:120]}")
    return _render_lucode_panel("模型能力探测", lines)


def render_status_command(
    project_root: Path,
    settings: RuntimeSettings,
    started_mcp_ids: list[str] | None = None,
    rollback_status: str = "",
) -> str:
    catalog = load_model_catalog()
    configured_count = sum(1 for item in catalog.get("models", []) if item.get("configured"))
    available_count = sum(1 for item in catalog.get("models", []) if _is_runtime_available(item, settings))
    git_summary = _git_status_summary(project_root)
    refiner = "开启" if settings.query_refiner_enabled else "关闭"
    mcp_text = ", ".join(started_mcp_ids or []) or "本轮尚未启动 MCP"
    lines = [
        f"当前模式：{execution_mode_label_zh(settings.execution_mode)}",
        f"隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        f"前置优化副脑：{refiner}",
        f"模型：已配置 {configured_count} 个，当前可用 {available_count} 个",
        f"已启动 MCP：{mcp_text}",
        f"Git 工作区：{git_summary}",
    ]
    if rollback_status:
        lines.append(rollback_status)
    return _render_lucode_panel("运行状态", lines)


def render_diff_command(project_root: Path, max_chars: int = 4000) -> str:
    stat_result = _run_git(project_root, ["diff", "--stat"], timeout_seconds=20)
    name_result = _run_git(project_root, ["diff", "--name-only"], timeout_seconds=20)
    diff_result = _run_git(project_root, ["diff", "--"], timeout_seconds=30)
    limit = max(1000, min(int(max_chars or 12000), 50000))
    lines = ["Diff 摘要"]
    if stat_result.returncode != 0:
        return "\n".join(lines + [f"git diff 不可用：{_stderr_or_stdout(stat_result)}"])
    stat = stat_result.stdout.strip()
    names = [line.strip() for line in name_result.stdout.splitlines() if line.strip()] if name_result.returncode == 0 else []
    if not stat and not names:
        lines.append("当前没有未暂存 diff。")
        return "\n".join(lines)
    if names:
        lines.append("变更文件：")
        for name in names[:20]:
            lines.append(f"- {name}")
        if len(names) > 20:
            lines.append(f"- 其余 {len(names) - 20} 个文件已省略。")
    if stat:
        lines.append("")
        lines.append("统计：")
        lines.append(stat)
    diff_text = diff_result.stdout if diff_result.returncode == 0 else ""
    if diff_text:
        lines.append("")
        lines.append("预览：")
        lines.append(_truncate(diff_text, limit))
    return "\n".join(lines)


def _render_config(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    local_models, cloud_models = _split_models(catalog["models"])

    lines = [
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        "操作入口：/models 进入调音台；/models list 查看来源；/connect 管理连接和删除。",
        "",
        "模型能力表",
        "",
        "本地模型",
    ]
    lines.extend(_render_model_block(local_models))
    lines.append("")
    lines.append("云端模型")
    lines.extend(_render_model_block(cloud_models))
    lines.append("")
    lines.append("提示：未探测表示还没有做真实能力测试；保守判断不会阻止你手动选择。")
    return _render_lucode_panel("Lucode 配置概览", lines)


def _render_api_show(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    lines = [
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
    ]
    for item in sorted(catalog["models"], key=lambda model: (not model.get("is_local"), model["id"])):
        lines.extend(_render_api_model_card(item))
    lines.append("")
    lines.append("说明：只显示地址和状态，不显示任何 API key。")
    return _render_lucode_panel("API 配置", lines)


def _render_connect(workspace_context=None) -> str:
    provider_catalog = load_provider_catalog()
    user_home = _user_home(workspace_context)
    workspace_root = _workspace_root(workspace_context)
    auth = load_auth(user_home=user_home)
    config = load_effective_lucode_config(workspace_root=workspace_root, user_home=user_home)
    configured_providers = config.get("provider") or {}
    auth_providers = auth.get("providers") or {}
    connected_ids = sorted(set(configured_providers) | set(auth_providers))

    lines = [
        f"用户凭据：{auth_path(user_home)}",
        f"项目配置：{project_config_path(workspace_root)}",
        "说明：API key 只保存到用户级 auth.json；项目里只保存 Provider 和模型名。",
        "",
        "已连接 Provider",
    ]
    if not connected_ids:
        lines.append("  无")
    for provider_id in connected_ids:
        provider_config = dict(provider_catalog.get(provider_id) or {})
        provider_config.update(configured_providers.get(provider_id) or {})
        display_name = provider_config.get("display_name") or provider_id
        homepage = provider_config.get("homepage") or "未配置"
        base_url = provider_config.get("base_url") or "未配置"
        key_state = "本地无需 key" if provider_config.get("local") else (
            "已保存 key" if provider_has_api_key(provider_id, user_home=user_home) else "未保存 key"
        )
        lines.append(f"  {display_name}（{provider_id}）  {key_state}")
        lines.append(f"    官网：{homepage}")
        lines.append(f"    请求地址：{base_url}")

    lines.extend(["", "可连接厂商"])
    for provider_id, item in sorted(provider_catalog.items()):
        display_name = item.get("display_name") or provider_id
        homepage = item.get("homepage") or "需自定义"
        base_url = item.get("base_url") or "需自定义"
        lines.append(f"  {display_name}（{provider_id}）")
        lines.append(f"    官网：{homepage}")
        lines.append(f"    请求地址：{base_url}")

    lines.extend(
        [
            "",
            "交互入口：聊天里输入 /connect，可用上下键/鼠标选择连接或删除模型/Provider。",
            "命令入口：lucode connect <provider> --api-key <key>",
            "自定义中转：lucode connect my_proxy --custom --homepage <官网> --base-url <请求地址> --api-key <key> --model <模型名>",
        ]
    )
    return _render_lucode_panel("Lucode Provider 连接", lines)


def _render_connect_provider_hint(command: str, workspace_context=None) -> str:
    parts = command.split(maxsplit=1)
    raw_provider_id = parts[1].strip() if len(parts) > 1 else ""
    try:
        provider_id = normalize_provider_id(raw_provider_id)
    except ValueError:
        provider_id = raw_provider_id
    catalog = load_provider_catalog()
    item = catalog.get(provider_id)
    if not item:
        return "\n".join(
            [
                f"Provider：{raw_provider_id or provider_id}",
                "未找到内置预设。若这是中转服务，请使用 lucode connect 的 --custom、--homepage、--base-url、--model 和 --api-key 参数配置。",
            ]
        )
    is_local = bool(item.get("local"))
    key_state = "本地无需 API key" if is_local else (
        "已保存 API key" if provider_has_api_key(provider_id, user_home=_user_home(workspace_context)) else "还缺 API key"
    )
    return "\n".join(
        [
            f"Provider：{item.get('display_name') or provider_id}",
            f"官网：{item.get('homepage') or '未配置'}",
            f"请求地址：{item.get('base_url') or '未配置'}",
            f"接口：{_format_backend_type(item.get('compatible_type'))}",
            f"状态：{key_state}",
            f"推荐模型：{', '.join(item.get('models') or []) or '需手动填写'}",
            "",
            f"连接命令：lucode connect {provider_id} --api-key <key>",
            "说明：API key 会写入用户级 auth.json，不会写入当前项目。",
        ]
    )


def _render_models(settings: RuntimeSettings, workspace_context=None) -> str:
    user_home = _user_home(workspace_context)
    workspace_root = _workspace_root(workspace_context)
    config = load_effective_lucode_config(workspace_root=workspace_root, user_home=user_home)
    provider_catalog = load_provider_catalog()
    configured_providers = config.get("provider") or {}
    auth_providers = (load_auth(user_home=user_home).get("providers") or {})
    provider_ids = sorted(set(provider_catalog) | set(configured_providers) | set(auth_providers))

    lines = [
        f"用户凭据：{auth_path(user_home)}",
        f"项目配置：{project_config_path(workspace_root)}",
        "说明：这里按模型来源分组；API key 不会在此处显示。",
    ]

    grouped_rows = {
        "configured_key": [],
        "missing_key": [],
        "local": [],
        "custom": [],
    }
    for provider_id in provider_ids:
        if provider_id == "custom_openai_compatible" and provider_id not in configured_providers:
            continue
        row = _provider_source_row(provider_id, provider_catalog, configured_providers, user_home)
        grouped_rows[_provider_source_group(row)].append(row)

    lines.extend(
        [
            "",
            *_render_provider_source_section("已配置 key", grouped_rows["configured_key"]),
            "",
            *_render_provider_source_section("缺 API key", grouped_rows["missing_key"]),
            "",
            *_render_provider_source_section("本地 Provider", grouped_rows["local"]),
            "",
            *_render_provider_source_section("自定义中转", grouped_rows["custom"]),
            "",
            "下一步：/models select provider/model，或 /models brain <脑位> provider/model。",
            "配置 key：/connect <provider> --api-key <key>",
            "自定义中转：/connect my_proxy --custom --homepage <官网> --base-url <请求地址> --api-key <key> --model <模型名>",
        ]
    )
    return _render_lucode_panel("Provider 模型列表", lines)


def _provider_source_row(provider_id: str, provider_catalog: dict, configured_providers: dict, user_home: Path) -> dict:
    provider_config = dict(provider_catalog.get(provider_id) or {})
    configured = configured_providers.get(provider_id) if isinstance(configured_providers, dict) else None
    if isinstance(configured, dict):
        provider_config.update(configured)
    provider_id = normalize_provider_id(provider_id)
    models = _nonempty_strings(provider_config.get("models") or [])
    has_key = provider_has_api_key(provider_id, user_home=user_home)
    is_local = bool(provider_config.get("local"))
    is_custom = (
        provider_id not in provider_catalog
        or provider_id == "custom_openai_compatible"
        or str(provider_config.get("cost_level") or "").strip().lower() == "custom"
    )
    return {
        "provider_id": provider_id,
        "display_name": str(provider_config.get("display_name") or provider_id),
        "homepage": str(provider_config.get("homepage") or "未配置"),
        "base_url": str(provider_config.get("base_url") or "未配置"),
        "backend": str(provider_config.get("compatible_type") or provider_config.get("backend_type") or "openai_compatible"),
        "models": models,
        "configured": isinstance(configured, dict),
        "has_key": has_key,
        "local": is_local,
        "custom": is_custom,
    }


def _provider_source_group(row: dict) -> str:
    if row.get("custom"):
        return "custom"
    if row.get("local"):
        return "local"
    if row.get("has_key"):
        return "configured_key"
    return "missing_key"


def _render_provider_source_section(title: str, rows: list[dict]) -> list[str]:
    lines = [title]
    if not rows:
        lines.append("  无")
        return lines
    for row in sorted(rows, key=lambda item: (str(item.get("display_name") or ""), str(item.get("provider_id") or ""))):
        lines.extend(_render_provider_source_card(row))
    return lines


def _render_provider_source_row(row: dict) -> list[str]:
    return _render_provider_source_card(row)


def _render_provider_source_card(row: dict) -> list[str]:
    provider_id = row.get("provider_id") or ""
    display_name = row.get("display_name") or provider_id
    models = row.get("models") or []
    configured_text = "项目已连接" if row.get("configured") else "项目未连接"
    if row.get("local"):
        key_text = "本地无需 key"
        next_step = f"下一步：/models select {_first_provider_ref(provider_id, models)}" if models else f"下一步：/connect {provider_id} --model <本地模型名>"
    elif row.get("has_key"):
        key_text = "已保存 key"
        next_step = f"下一步：/models select {_first_provider_ref(provider_id, models)}" if models else f"下一步：/connect {provider_id} --model <模型名>"
    else:
        key_text = "缺 API key"
        next_step = f"下一步：/connect {provider_id} --api-key <key>"

    model_refs = _provider_model_refs(provider_id, models)
    return [
        f"  {display_name}（{provider_id}）  {key_text} · {configured_text} · {_format_backend_type(row.get('backend'))}",
        f"    地址：{row.get('base_url') or '未配置'}",
        f"    模型：{', '.join(model_refs) if model_refs else '未配置'}",
        f"    {next_step}",
    ]


def _provider_model_refs(provider_id: str, models: list[str]) -> list[str]:
    refs = [f"{provider_id}/{model}" for model in _nonempty_strings(models)]
    if len(refs) <= 2:
        return refs
    remaining = len(refs) - 2
    return [*refs[:2], f"另有 {remaining} 个"]


def _first_provider_ref(provider_id: str, models: list[str]) -> str:
    names = _nonempty_strings(models)
    if not names:
        return f"{provider_id}/<model>"
    return f"{provider_id}/{names[0]}"


def _nonempty_strings(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    result = []
    for item in values:
        value = str(item or "").strip()
        if value:
            result.append(value)
    return result


def _render_model_roles(settings: RuntimeSettings, workspace_context=None) -> str:
    config = load_effective_lucode_config(
        workspace_root=_workspace_root(workspace_context),
        user_home=_user_home(workspace_context),
    )
    roles = config.get("roles") or {}
    lines = [
        "可用角色：query_refiner、orchestrator、executor、final_synthesizer",
        "",
        "当前运行时优先级",
        f"- query_refiner：{_compact_role_priority(settings.query_refiner_model_priority)}",
        f"- orchestrator：{_compact_role_priority(settings.orchestrator_model_priority)}",
        f"- executor：{_compact_role_priority(settings.executor_model_priority)}",
        f"- final_synthesizer：{_compact_role_priority(settings.final_synthesizer_model_priority)}",
        "",
        "项目配置 roles",
    ]
    if isinstance(roles, dict) and roles:
        for role_id in ["query_refiner", "orchestrator", "executor", "final_synthesizer"]:
            value = roles.get(role_id)
            label = model_role_label(role_id)
            lines.append(f"- {label}（{role_id}）：{', '.join(value) if isinstance(value, list) else value or '未配置'}")
    else:
        lines.append("- 未配置；默认使用 /models select 的主模型和 fallback。")
    lines.extend(
        [
            "",
            "写入命令：/models brain <脑位> provider/model [fallback...]",
            "示例：/models brain 主脑 deepseek/deepseek-chat openrouter/openai/gpt-4o",
        ]
    )
    return _render_lucode_panel("四脑角色模型配置", lines)


def _compact_role_priority(values: list[str]) -> str:
    items = [str(item).strip() for item in values or [] if str(item).strip()]
    if not items:
        return "未设置"
    if len(items) <= 2:
        return ", ".join(items)
    return f"{items[0]}, {items[1]}，另有 {len(items) - 2} 个"


def _render_model_brains(settings: RuntimeSettings, workspace_context=None) -> str:
    state = build_model_tuner_state(settings, workspace_context)
    return render_model_tuner_snapshot(
        state,
        max_options=4,
        message="交互式终端输入 /models 会进入独立调音台；非交互环境仅显示这个快照。",
    )


def _render_privacy(settings: RuntimeSettings) -> str:
    policy = PrivacyPolicy(settings.privacy_mode)
    return _render_lucode_panel(
        "隐私模式状态",
        [
            "只读查看",
            f"当前模式：{_format_privacy_mode(policy.mode)}",
            f"允许云端模型：{'是' if policy.allows_cloud_models else '否'}",
            f"允许联网 MCP：{'是' if policy.allows_network_tools else '否'}",
            "",
            "可选模式：离线模式 / 本地优先 / 允许云端",
            "对应配置值：offline / local_first / cloud_allowed",
            "说明：隐私模式当前仍是只读查看，后续会再加入一键切换。",
        ],
    )


def _render_mode(settings: RuntimeSettings) -> str:
    return _render_lucode_panel(
        "执行模式状态",
        [
            f"当前模式：{execution_mode_label_zh(settings.execution_mode)}",
            "",
            "可选模式：solo / serial / full",
            "solo：默认单模型工具 Agent，可以读写文件、联网、跑命令和测试，但不创建多 Agent。",
            "serial：显式多 Agent 串行工程模式，由主脑规划，多专家按顺序处理。",
            "full：显式高级并行多 Agent，只有通过安全门的批次才允许并行。",
            "",
            "说明：输入 /mode solo、/mode serial 或 /mode full 可立即切换并写入 .env。",
        ],
    )


def _render_model(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    model_names = {item["id"]: item for item in catalog.get("models", [])}
    lines = [
        "只读查看",
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        "",
        "前置优化脑",
    ]
    lines.extend(_render_model_priority_block(settings.query_refiner_model_priority, model_names))
    lines.append("")
    lines.append("主脑规划脑")
    lines.extend(_render_model_priority_block(settings.orchestrator_model_priority, model_names))
    lines.append("")
    lines.append("执行专家脑")
    lines.extend(_render_model_priority_block(settings.executor_model_priority, model_names))
    lines.append("")
    lines.append("汇总脑")
    lines.extend(_render_model_priority_block(settings.final_synthesizer_model_priority, model_names))
    candidate_lines = _render_priority_candidate_block(catalog.get("models", []), settings)
    if candidate_lines:
        lines.append("")
        lines.append("可加入优先级的候选模型")
        lines.extend(candidate_lines)
    unavailable_lines = _render_unavailable_model_block(catalog.get("models", []), settings)
    if unavailable_lines:
        lines.append("")
        lines.append("暂不可用模型")
        lines.extend(unavailable_lines)
    lines.append("")
    lines.append("说明：模型优先级当前仍是只读查看，后续会再加入一键切换。")
    return _render_lucode_panel("模型优先级", lines)


def _render_model_available(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    available_models = [
        item for item in catalog.get("models", []) if item.get("configured") and _is_runtime_available(item, settings)
    ]
    lines = [
        "紧凑视图",
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
    ]
    if not available_models:
        lines.extend(
            [
                "",
                "当前没有确认可用的模型。",
                "你可以先用 /config 或 /api show 检查模型连接状态。",
            ]
        )
        return _render_lucode_panel("可用模型（紧凑视图）", lines)

    for item in sorted(available_models, key=lambda model: (not model.get("is_local"), model.get("id") or "")):
        lines.append(
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))} | "
            f"{_format_model_probe_badges(item)}"
        )
    lines.append("")
    lines.append("说明：这里只显示当前可运行的模型。")
    return _render_lucode_panel("可用模型（紧凑视图）", lines)


def _render_readonly_switch_hint(command_name: str, value: str) -> str:
    if command_name == "/mode":
        return "\n".join(
            [
                f"/mode 切换请求：{value}",
                "当前 /mode 支持直接切换：/mode solo、/mode serial、/mode full。",
            ]
        )
    if command_name == "/models":
        return "\n".join(
            [
                f"/models 请求：{value}",
                "查看模型请用 /models；切换主模型请用 /models select provider/model [fallback...]。",
            ]
        )
    if command_name == "/model" and value in {"solo", "serial", "full"}:
        return "\n".join(
            [
                f"/model 检测到可能的执行模式：{value}",
                "如果你要切换 solo / serial / full，请使用 /mode 命令。",
                f"正确命令：/mode {value}",
            ]
        )
    return "\n".join(
        [
            f"{command_name} 切换请求：{value}",
            "当前版本不会直接改写 .env，这里只做只读提示。",
            "如果你要真正切换，请手动修改 .env 后重新启动程序。",
        ]
    )


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        current_key = line.split("=", 1)[0].strip()
        if current_key == key:
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _git_status_summary(project_root: Path) -> str:
    result = _run_git(project_root, ["status", "--short"], timeout_seconds=20)
    if result.returncode != 0:
        return f"不可用：{_stderr_or_stdout(result)}"
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return "干净"
    return f"{len(lines)} 个改动/未跟踪文件"


def _run_git(project_root: Path, args: list[str], timeout_seconds: int = 30) -> subprocess.CompletedProcess:
    try:
        if not project_root.is_dir():
            return subprocess.CompletedProcess(
                ["git", *args],
                1,
                "",
                f"workspace is not a directory: {project_root}",
            )
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", f"workspace is not accessible: {exc}")

    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git executable was not found in PATH.")
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(["git", *args], 124, exc.stdout or "", "git command timed out.")


def _stderr_or_stdout(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "无详细输出").strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[已截断 {len(value) - limit} 字符]"


def _parse_model_select_value(value: str) -> tuple[str, list[str]]:
    refs = [item for item in re.split(r"[\s,]+", str(value or "").strip()) if item]
    if not refs:
        raise ValueError("请提供模型引用，例如 deepseek/deepseek-chat。")
    return refs[0], refs[1:]


def _parse_model_role_value(value: str) -> tuple[str, list[str]]:
    parts = [item for item in re.split(r"[\s,]+", str(value or "").strip()) if item]
    if len(parts) < 2:
        raise ValueError("请提供角色和至少一个模型引用，例如 /models role orchestrator deepseek/deepseek-chat。")
    return parts[0], parts[1:]


def _apply_role_priority_to_settings(settings: RuntimeSettings, role: str, model_ids: list[str]) -> None:
    normalized = str(role or "").strip().lower().replace("-", "_")
    if normalized in {"refiner", "query_refiner", "前置优化", "前置优化副脑"}:
        settings.query_refiner_model_priority = list(model_ids)
    elif normalized in {"planner", "main", "main_brain", "orchestrator", "主脑"}:
        settings.orchestrator_model_priority = list(model_ids)
    elif normalized in {"executor", "execution", "worker", "agent", "specialist", "solo", "执行", "执行脑", "执行专家", "专家脑", "solo_agent"}:
        settings.executor_model_priority = list(model_ids)
    elif normalized in {"synthesizer", "final", "final_brain", "final_synthesizer", "汇总", "汇总脑", "汇总副脑"}:
        settings.final_synthesizer_model_priority = list(model_ids)


def _reload_model_priorities(settings: RuntimeSettings, workspace_context=None) -> None:
    old_workspace = os.environ.get("LUCODE_WORKSPACE_ROOT")
    old_user_home = os.environ.get("LUCODE_USER_HOME")
    try:
        if workspace_context is not None:
            os.environ["LUCODE_WORKSPACE_ROOT"] = str(_workspace_root(workspace_context))
            os.environ["LUCODE_USER_HOME"] = str(_user_home(workspace_context))
        fresh = RuntimeSettings.from_env()
    finally:
        if old_workspace is None:
            os.environ.pop("LUCODE_WORKSPACE_ROOT", None)
        else:
            os.environ["LUCODE_WORKSPACE_ROOT"] = old_workspace
        if old_user_home is None:
            os.environ.pop("LUCODE_USER_HOME", None)
        else:
            os.environ["LUCODE_USER_HOME"] = old_user_home

    settings.query_refiner_model_priority = list(fresh.query_refiner_model_priority)
    settings.orchestrator_model_priority = list(fresh.orchestrator_model_priority)
    settings.executor_model_priority = list(fresh.executor_model_priority)
    settings.final_synthesizer_model_priority = list(fresh.final_synthesizer_model_priority)


_KNOWN_COMMAND_PREFIXES = known_command_prefixes()


def _workspace_root(workspace_context=None) -> Path:
    value = getattr(workspace_context, "workspace_root", None)
    if value is not None:
        return Path(value).resolve()
    env_value = os.environ.get("LUCODE_WORKSPACE_ROOT")
    if env_value:
        return Path(env_value).resolve()
    return Path.cwd().resolve()


def _user_home(workspace_context=None) -> Path:
    value = getattr(workspace_context, "user_home", None)
    if value is not None:
        return Path(value).resolve()
    env_value = os.environ.get("LUCODE_USER_HOME")
    if env_value:
        return Path(env_value).resolve()
    return (Path.home() / ".lucode").resolve()


def _model_label(model_id: str, model_infos: dict[str, dict]) -> str:
    info = model_infos.get(model_id) or {}
    if info.get("provider_ref"):
        return str(info["provider_ref"])
    if info:
        return _format_model_title(info)
    return model_id or "未设置"


def _compact_model_label(model_id: str, model_infos: dict[str, dict], max_chars: int = 42) -> str:
    label = _model_label(model_id, model_infos).replace("（", " (").replace("）", ")")
    if len(label) <= max_chars:
        return label
    return f"{label[: max_chars - 1]}…"


def _fallback_summary(model_ids: list[str], model_infos: dict[str, dict]) -> str:
    fallback = list(model_ids or [])[1:]
    if not fallback:
        return "-"
    first = _compact_model_label(fallback[0], model_infos, max_chars=32)
    if len(fallback) == 1:
        return first
    return f"{first} +{len(fallback) - 1}"


def _split_models(models: list[dict]) -> tuple[list[dict], list[dict]]:
    local_models = [item for item in models if item.get("is_local")]
    cloud_models = [item for item in models if not item.get("is_local")]
    return local_models, cloud_models


def _render_model_block(models: list[dict]) -> list[str]:
    if not models:
        return ["  无"]
    lines: list[str] = []
    for item in models:
        lines.extend(_render_config_model_panel_card(item))
    return lines


def _render_config_model_panel_card(model_info: dict) -> list[str]:
    return [
        f"  {_format_model_title(model_info)}",
        (
            "    状态："
            f"{_format_backend_type(model_info.get('backend_type'))} · "
            f"{_format_configured(model_info.get('configured'))} · "
            f"{_format_availability(model_info)} · "
            f"{_format_privacy_level(model_info.get('privacy_level'))}"
        ),
        (
            "    能力 "
            f"工具 {_format_tool_support(model_info)} · "
            f"主脑 {_format_planner_suitability(model_info)} · "
            f"执行 {_format_execution_suitability(model_info)}"
        ),
        f"    探测：{_format_probe_status(model_info)}",
    ]


def _render_lucode_panel(title: str, lines: list[str], *, width: int = PANEL_WIDTH) -> str:
    body_width = _resolved_panel_body_width(width)
    rendered = [_panel_top(title, body_width)]
    for line in lines:
        if line == "":
            rendered.append(_panel_line("", body_width))
            continue
        if _is_section_heading(line):
            rendered.append(_panel_section(line, body_width))
            continue
        for wrapped in _wrap_visible(line, body_width):
            rendered.append(_panel_line(wrapped, body_width))
    rendered.append(_panel_bottom(body_width))
    return "\n".join(rendered)


def _resolved_panel_body_width(width: int) -> int:
    columns = shutil.get_terminal_size((width + 10, 24)).columns
    safe_width = max(60, min(int(width or PANEL_WIDTH), columns - 10))
    return max(48, safe_width - 4)


def _panel_top(title: str, body_width: int) -> str:
    label = f" {title} "
    line = "╭─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "╮"
    return _ansi_blue(line)


def _panel_bottom(body_width: int) -> str:
    return _ansi_blue("╰" + "─" * (body_width + 2) + "╯")


def _panel_section(title: str, body_width: int) -> str:
    label = f" {title} "
    line = "├─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "┤"
    return _ansi_blue(line)


def _panel_line(value: str, body_width: int) -> str:
    return f"{_ansi_blue('│')} {value}{' ' * max(0, body_width - _display_width(value))} {_ansi_blue('│')}"


def _is_section_heading(value: str) -> bool:
    text = str(value or "").strip()
    return text in {
        "模型能力表",
        "本地模型",
        "云端模型",
        "已连接 Provider",
        "可连接厂商",
        "已配置 key",
        "缺 API key",
        "本地 Provider",
        "自定义中转",
        "当前运行时优先级",
        "项目配置 roles",
        "前置优化脑",
        "主脑规划脑",
        "执行专家脑",
        "汇总脑",
        "可加入优先级的候选模型",
        "暂不可用模型",
    }


def _wrap_visible(value: str, width: int) -> list[str]:
    text = str(value or "")
    if _display_width(text) <= width:
        return [text]
    indent = len(text) - len(text.lstrip(" "))
    prefix = " " * min(indent, 6)
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in text:
        char_width = _display_width(char)
        if current and current_width + char_width > width:
            lines.append(current.rstrip())
            current = prefix + char.lstrip() if char == " " else prefix + char
            current_width = _display_width(current)
            continue
        current += char
        current_width += char_width
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _ansi_blue(value: str) -> str:
    if os.environ.get("NO_COLOR"):
        return value
    return f"{LUCODE_BLUE}{value}{ANSI_RESET}"


def _render_table(headers: list[str], rows: list[list[str]], *, max_widths: list[int] | None = None) -> list[str]:
    if not rows:
        return ["无"]
    string_rows = [[str(cell or "") for cell in row] for row in rows]
    max_widths = max_widths or [40] * len(headers)
    widths: list[int] = []
    for index, header in enumerate(headers):
        cells = [header, *[row[index] if index < len(row) else "" for row in string_rows]]
        target = max(_display_width(cell) for cell in cells)
        limit = max_widths[index] if index < len(max_widths) else 40
        widths.append(max(2, min(target, limit)))

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * (width + 2) for width in widths) + right

    def row_line(values: list[str]) -> str:
        cells = []
        for index, width in enumerate(widths):
            value = values[index] if index < len(values) else ""
            fitted = _fit_cell(value, width)
            cells.append(f" {fitted}{' ' * max(0, width - _display_width(fitted))} ")
        return "│" + "│".join(cells) + "│"

    lines = [border("┌", "┬", "┐"), row_line(headers), border("├", "┼", "┤")]
    lines.extend(row_line(row) for row in string_rows)
    lines.append(border("└", "┴", "┘"))
    return lines


def _fit_cell(value: str, width: int) -> str:
    text = str(value or "")
    if _display_width(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    result = ""
    used = 0
    for char in text:
        char_width = _display_width(char)
        if used + char_width > width - 1:
            break
        result += char
        used += char_width
    return result + "…"


def _display_width(value: str) -> int:
    width = 0
    for char in str(value or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _render_config_model_card(model_info: dict) -> list[str]:
    return [
        f"- {_format_model_title(model_info)}",
        (
            "  基础："
            f"{_format_backend_type(model_info.get('backend_type'))} | "
            f"{_format_configured(model_info.get('configured'))} | "
            f"{_format_availability(model_info)} | "
            f"{_format_privacy_level(model_info.get('privacy_level'))}"
        ),
        (
            "  能力："
            f"工具 {_format_tool_support(model_info)} | "
            f"主脑 {_format_planner_suitability(model_info)} | "
            f"执行 {_format_execution_suitability(model_info)}"
        ),
        f"  探测：{_format_probe_status(model_info)}",
    ]


def _render_api_model_card(model_info: dict) -> list[str]:
    return [
        f"- {_format_model_title(model_info)}",
        f"  接口：{_format_backend_type(model_info.get('backend_type'))}",
        f"  地址：{_safe_base_url(model_info)}",
        (
            "  状态："
            f"{_format_configured(model_info.get('configured'))} | "
            f"{_format_availability(model_info)} | "
            f"{_format_privacy_level(model_info.get('privacy_level'))}"
        ),
    ]


def _render_model_priority_block(model_ids: list[str], model_infos: dict[str, dict]) -> list[str]:
    if not model_ids:
        return ["- 未设置"]
    lines = []
    visible_index = 1
    for model_id in model_ids:
        info = model_infos.get(model_id) or {}
        if info:
            lines.append(
                f"{visible_index}. {_format_model_title(info)} | "
                f"{_format_configured(info.get('configured'))} | "
                f"{_format_availability(info)} | "
                f"{_format_backend_type(info.get('backend_type'))} | "
                f"{_format_privacy_level(info.get('privacy_level'))}"
            )
            visible_index += 1
    return lines or ["- 当前优先级里的模型都没有在 .env 注册"]


def _render_priority_candidate_block(models: list[dict], settings: RuntimeSettings) -> list[str]:
    priority_ids = set(settings.query_refiner_model_priority)
    priority_ids.update(settings.orchestrator_model_priority)
    priority_ids.update(settings.executor_model_priority)
    priority_ids.update(settings.final_synthesizer_model_priority)
    candidates = [
        item
        for item in models
        if item.get("configured")
        and item.get("id") not in priority_ids
        and _is_runtime_available(item, settings)
    ]
    if not candidates:
        return []

    lines = []
    for item in sorted(candidates, key=lambda model: (model.get("is_local") is not True, model.get("id") or "")):
        roles = _suggest_priority_roles(item)
        lines.extend(
            [
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_configured(item.get('configured'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))}",
            f"  建议角色：{', '.join(roles)}",
            ]
        )
    return lines


def _render_unavailable_model_block(models: list[dict], settings: RuntimeSettings) -> list[str]:
    unavailable = [
        item
        for item in models
        if item.get("id") and item.get("configured") and not _is_runtime_available(item, settings)
    ]
    if not unavailable:
        return []

    lines = []
    for item in sorted(unavailable, key=lambda model: (model.get("is_local") is not True, model.get("id") or "")):
        lines.extend(
            [
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_configured(item.get('configured'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))}",
            f"  处理建议：{_unavailable_reason(item, settings)}",
            ]
        )
    return lines


def _unavailable_reason(model_info: dict, settings: RuntimeSettings) -> str:
    if not model_info.get("configured"):
        return "补全 .env 中的地址、模型名和 API key 后再使用"
    if not PrivacyPolicy(settings.privacy_mode).model_allowed(model_info):
        return "当前隐私模式不允许使用该模型"
    if _availability_blocks_runtime(model_info):
        status = str((model_info.get("probe") or {}).get("status") or "")
        if status == "service_unavailable":
            return "Ollama 服务未连通，请确认 Ollama 已启动且 base_url 正确"
        if status == "model_missing":
            return "Ollama 服务在线，但没有找到该模型，请先执行 ollama pull 或修改模型名"
        if model_info.get("is_local"):
            return "Ollama 服务在线，但模型能力探测失败；可调大 MODEL_PROBE_TIMEOUT_SECONDS 后重启"
        return "先检查接口地址、网络或 API key"
    return "当前未满足运行条件"


def _suggest_priority_roles(model_info: dict) -> list[str]:
    if _availability_blocks_runtime(model_info):
        return ["暂不可用，先检查连接"]
    roles = []
    best_for = set(model_info.get("best_for_skills") or [])
    reasoning = str(model_info.get("reasoning_level") or "").lower()
    tier = str(model_info.get("model_tier") or "").lower()
    if best_for.intersection({"project_explorer", "humanizer_zh"}) or tier in {"small", "medium", "large"}:
        roles.append("前置优化脑")
    if reasoning == "high" or tier in {"large", "medium"}:
        roles.append("主脑规划脑")
    if "jpc_now_skill" in best_for:
        roles.append("执行专家脑")
    if reasoning == "high" or tier in {"large", "medium"}:
        roles.append("汇总脑")
    if not roles:
        roles.append("按任务手动选择")
    return roles


def _availability_blocks_runtime(model_info: dict) -> bool:
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
        return True
    return bool(model_info.get("is_local") and not status)


def _is_runtime_available(model_info: dict, settings: RuntimeSettings) -> bool:
    if not model_info.get("configured"):
        return False
    if not PrivacyPolicy(settings.privacy_mode).model_allowed(model_info):
        return False
    return not _availability_blocks_runtime(model_info)


def _format_model_title(model_info: dict) -> str:
    display_name = model_info.get("display_name_zh") or "未命名模型"
    return f"{display_name}（{model_info.get('id') or 'unknown'}）"


def _format_backend_type(value: str | None) -> str:
    labels = {
        "openai": "OpenAI 官方接口",
        "openai_compatible": "OpenAI 兼容接口",
        "ollama": "Ollama 本地服务",
        "llama_cpp": "llama.cpp 本地原生",
    }
    backend = str(value or "").strip()
    return labels.get(backend, backend or "未知接口")


def _format_configured(value) -> str:
    if value is True:
        return "配置完整"
    if value is False:
        return "配置不完整"
    return "未知"


def _format_availability(model_info: dict) -> str:
    if not model_info.get("configured"):
        return "不可用"
    probe = model_info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    if status == "ok":
        return "连接可用"
    if status == "partial":
        return "部分可用"
    if status == "capability_probe_failed" and probe.get("service_available") is True:
        return "服务在线，能力探测失败"
    if status in {"chat_failed", "probe_failed"}:
        return "连接不可用"
    if status == "service_unavailable":
        return "本地服务未连通"
    if status == "model_missing":
        return "服务在线，模型未安装"
    if status == "tools_unsupported":
        return "可聊天，不支持工具"
    if status == "config_incomplete":
        return "配置不完整"
    if model_info.get("is_local"):
        return "未确认可用"
    return "未探测"


def _format_privacy_level(value: str | None) -> str:
    labels = {
        "local": "本地",
        "local_native": "本地原生",
        "cloud": "云端",
    }
    level = str(value or "").strip()
    return labels.get(level, level or "未知")


def _format_privacy_mode(value: str | None) -> str:
    labels = {
        "offline": "离线模式",
        "local_first": "本地优先",
        "cloud_allowed": "允许云端",
    }
    mode = str(value or "").strip()
    if mode in labels:
        return labels[mode]
    return mode or "未知"


def _format_tool_support(model_info: dict) -> str:
    value = model_info.get("supports_tools")
    if not _has_probe(model_info):
        return _format_bool_zh(value, unknown="未知（未探测）", suffix="（保守判断）")
    return _format_bool_zh(value)


def _format_planner_suitability(model_info: dict) -> str:
    value = model_info.get("planner_suitable")
    if value is not None:
        return _format_bool_zh(value)
    if not model_info.get("configured"):
        return "否（未配置）"
    if not _has_probe(model_info):
        return "可尝试（未探测）"
    return "未知"


def _format_execution_suitability(model_info: dict) -> str:
    value = model_info.get("execution_suitable")
    if value is not None:
        return _format_bool_zh(value)
    if not model_info.get("configured"):
        return "否（未配置）"
    if not _has_probe(model_info):
        return _format_bool_zh(model_info.get("supports_tools"), unknown="未知（未探测）", suffix="（保守判断）")
    return "未知"


def _format_probe_status(model_info: dict) -> str:
    probe = model_info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    if not status:
        return "未探测（使用保守判断）"
    status_labels = {
        "ok": "正常",
        "tools_unsupported": "不支持工具调用",
        "chat_failed": "基础聊天失败",
        "probe_failed": "探测失败",
        "capability_probe_failed": "服务在线，能力探测失败",
        "partial": "部分探测成功",
        "service_unavailable": "本地服务未连通",
        "model_missing": "服务在线，模型未安装",
        "config_incomplete": "配置不完整",
        "disabled": "已关闭",
    }
    return status_labels.get(status, status)


def _format_tools_probe_summary(probe: dict) -> str:
    if not probe:
        return "未知"
    if probe.get("supports_tools") is True:
        parts = []
        if probe.get("tools_auto_call") is True:
            parts.append("auto")
        if probe.get("tools_forced_choice") is True:
            parts.append("强制")
        elif "forced" in (probe.get("probe_tool_choice_modes") or []) and probe.get("tools_api_accepted") is True:
            parts.append("不支持强制")
        if probe.get("tools_result_roundtrip") is True:
            parts.append("回填")
        return "是（" + "、".join(parts or ["已探测"]) + "）"
    if probe.get("supports_tools") is False:
        if probe.get("tools_api_accepted") is True:
            return "否（接口接受但未触发）"
        return "否"
    return "未知"


def _format_model_probe_badges(model_info: dict) -> str:
    badges: list[str] = []
    roles = model_info.get("recommended_roles") or (model_info.get("probe") or {}).get("recommended_roles") or []
    role_labels = {
        "query_refiner": "前置",
        "orchestrator": "主脑",
        "executor": "执行",
        "final_synthesizer": "汇总",
    }
    for role in ["query_refiner", "orchestrator", "executor", "final_synthesizer"]:
        if role in roles:
            badges.append(role_labels[role])
    context_label = _format_context_window(model_info)
    if context_label:
        badges.append(context_label)
    latency_label = _format_latency(model_info.get("latency_ms") or (model_info.get("probe") or {}).get("latency_ms"))
    if latency_label:
        badges.append(latency_label)
    return " · ".join(badges) if badges else "能力未探测"


def _format_context_window(model_info: dict) -> str:
    tokens = model_info.get("context_window_tokens") or (model_info.get("probe") or {}).get("context_window_tokens")
    try:
        value = int(tokens)
    except (TypeError, ValueError):
        return ""
    if value >= 1_000_000:
        return f"{value // 1_000_000}M"
    if value >= 1024:
        return f"{round(value / 1024):g}K"
    return str(value)


def _format_latency(value) -> str:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return f"{ms / 1000:.2f}s"


def _format_bool_zh(value, unknown: str = "未知", suffix: str = "") -> str:
    if value is True:
        return f"是{suffix}"
    if value is False:
        return f"否{suffix}"
    return unknown


def _has_probe(model_info: dict) -> bool:
    return bool((model_info.get("probe") or {}).get("status"))


def _safe_base_url(model_info: dict) -> str:
    backend = model_info.get("backend_type") or ""
    resolved_base_url = model_info.get("base_url") or ""
    if resolved_base_url:
        return resolved_base_url
    model_id = model_info.get("id") or ""
    env_name = _base_url_env_name(model_id)
    base_url = os.environ.get(env_name) or ""
    if not base_url and backend == "llama_cpp":
        return "本地原生推理预留"
    return base_url or "未配置"


def _base_url_env_name(model_id: str) -> str:
    if model_id == "deepseek_V4_flash_model":
        return "DEEPSEEK_BASE_URL"
    if model_id == "deepseek_V4_pro_model":
        return "DEEPSEEK_BASE_pro_URL"
    if model_id == "mimo_model":
        return "MIMO_API_BASE_URL"
    prefix = model_id.removesuffix("_model").upper()
    return f"MODEL_{prefix}_BASE_URL"
