from __future__ import annotations

import os
import subprocess
from pathlib import Path

from catalog_system.model_catalog import load_model_catalog
from runtime.config.execution_mode import execution_mode_label_zh
from runtime.safety.privacy import PrivacyPolicy
from runtime.config.settings import RuntimeSettings


def render_readonly_command(command: str, settings: RuntimeSettings) -> str:
    normalized = (command or "").strip()
    lower = normalized.lower()

    if lower == "/config":
        return _render_config(settings)
    if lower == "/api show":
        return _render_api_show(settings)
    if lower == "/privacy":
        return _render_privacy(settings)
    if lower == "/mode":
        return _render_mode(settings)
    if lower == "/model":
        return _render_model(settings)
    if lower == "/model available":
        return _render_model_available(settings)
    if lower.startswith("/privacy "):
        return _render_readonly_switch_hint("/privacy", normalized.split(maxsplit=1)[1])
    if lower.startswith("/mode "):
        return _render_readonly_switch_hint("/mode", normalized.split(maxsplit=1)[1])
    if lower.startswith("/model ") and lower != "/model available":
        return _render_readonly_switch_hint("/model", normalized.split(maxsplit=1)[1])
    if lower.startswith("/api "):
        return _render_readonly_switch_hint("/api", normalized.split(maxsplit=1)[1])
    return ""


def parse_writable_config_command(command: str) -> tuple[str, str] | None:
    normalized = (command or "").strip()
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
) -> tuple[str, bool]:
    parsed = parse_writable_config_command(command)
    if parsed is None:
        return (
            "无法识别这个配置切换命令。\n"
            "可用命令：/mode solo、/mode serial、/mode full、/refiner on、/refiner off",
            False,
        )

    kind, value = parsed
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
        "运行状态",
        f"当前模式：{execution_mode_label_zh(settings.execution_mode)}",
        f"隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        f"前置优化副脑：{refiner}",
        f"模型：已配置 {configured_count} 个，当前可用 {available_count} 个",
        f"已启动 MCP：{mcp_text}",
        f"Git 工作区：{git_summary}",
    ]
    if rollback_status:
        lines.append(rollback_status)
    return "\n".join(lines)


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
        "当前配置总览",
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        "",
        "本地模型",
    ]
    lines.extend(_render_model_block(local_models))
    lines.append("")
    lines.append("云端模型")
    lines.extend(_render_model_block(cloud_models))
    lines.append("")
    lines.append("说明：这是只读查看命令。切换执行模式请用 /mode solo|serial|full，切换前置优化请用 /refiner on|off。")
    return "\n".join(lines)


def _render_api_show(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    lines = [
        "API 配置",
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
    ]
    for item in sorted(catalog["models"], key=lambda model: (not model.get("is_local"), model["id"])):
        lines.extend(_render_api_model_card(item))
    lines.append("")
    lines.append("说明：只显示地址和状态，不显示任何 API key。")
    return "\n".join(lines)


def _render_privacy(settings: RuntimeSettings) -> str:
    policy = PrivacyPolicy(settings.privacy_mode)
    return "\n".join(
        [
            "隐私模式状态（只读查看）",
            f"当前模式：{_format_privacy_mode(policy.mode)}",
            f"允许云端模型：{'是' if policy.allows_cloud_models else '否'}",
            f"允许联网 MCP：{'是' if policy.allows_network_tools else '否'}",
            "",
            "可选模式：离线模式 / 本地优先 / 允许云端",
            "对应配置值：offline / local_first / cloud_allowed",
            "说明：隐私模式当前仍是只读查看，后续会再加入一键切换。",
        ]
    )


def _render_mode(settings: RuntimeSettings) -> str:
    return "\n".join(
        [
            "执行模式状态",
            f"当前模式：{execution_mode_label_zh(settings.execution_mode)}",
            "",
            "可选模式：solo / serial / full",
            "solo：默认单模型工具 Agent，可以读写文件、联网、跑命令和测试，但不创建多 Agent。",
            "serial：显式多 Agent 串行工程模式，由主脑规划，多专家按顺序处理。",
            "full：显式高级并行多 Agent，只有通过安全门的批次才允许并行。",
            "",
            "说明：输入 /mode solo、/mode serial 或 /mode full 可立即切换并写入 .env。",
        ]
    )


def _render_model(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    model_names = {item["id"]: item for item in catalog.get("models", [])}
    lines = [
        "模型优先级（只读查看）",
        f"当前隐私模式：{_format_privacy_mode(settings.privacy_mode)}",
        "",
        "前置优化副脑",
    ]
    lines.extend(_render_model_priority_block(settings.query_refiner_model_priority, model_names))
    lines.append("")
    lines.append("主脑模型优先级")
    lines.extend(_render_model_priority_block(settings.orchestrator_model_priority, model_names))
    lines.append("")
    lines.append("汇总副脑")
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
    return "\n".join(lines)


def _render_model_available(settings: RuntimeSettings) -> str:
    catalog = load_model_catalog()
    available_models = [
        item for item in catalog.get("models", []) if item.get("configured") and _is_runtime_available(item, settings)
    ]
    lines = [
        "可用模型（紧凑视图）",
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
        return "\n".join(lines)

    for item in sorted(available_models, key=lambda model: (not model.get("is_local"), model.get("id") or "")):
        lines.append(
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))}"
        )
    lines.append("")
    lines.append("说明：这里只显示当前可运行的模型。")
    return "\n".join(lines)


def _render_readonly_switch_hint(command_name: str, value: str) -> str:
    if command_name == "/mode":
        return "\n".join(
            [
                f"/mode 切换请求：{value}",
                "当前 /mode 支持直接切换：/mode solo、/mode serial、/mode full。",
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
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(["git", *args], 124, exc.stdout or "", "git command timed out.")


def _stderr_or_stdout(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "无详细输出").strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[已截断 {len(value) - limit} 字符]"


def _split_models(models: list[dict]) -> tuple[list[dict], list[dict]]:
    local_models = [item for item in models if item.get("is_local")]
    cloud_models = [item for item in models if not item.get("is_local")]
    return local_models, cloud_models


def _render_model_block(models: list[dict]) -> list[str]:
    if not models:
        return ["- 无"]
    lines = []
    for item in models:
        lines.extend(_render_config_model_card(item))
    return lines


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
        lines.append(
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_configured(item.get('configured'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))} | "
            f"建议角色：{', '.join(roles)}"
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
        lines.append(
            f"- {_format_model_title(item)} | "
            f"{_format_backend_type(item.get('backend_type'))} | "
            f"{_format_configured(item.get('configured'))} | "
            f"{_format_availability(item)} | "
            f"{_format_privacy_level(item.get('privacy_level'))} | "
            f"处理建议：{_unavailable_reason(item, settings)}"
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
        roles.append("前置优化副脑")
    if reasoning == "high" or tier in {"large", "medium"}:
        roles.append("主脑模型优先级")
    if reasoning == "high" or tier in {"large", "medium"}:
        roles.append("汇总副脑")
    if not roles:
        roles.append("按任务手动选择")
    return roles


def _availability_blocks_runtime(model_info: dict) -> bool:
    probe = model_info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    if status in {"chat_failed", "probe_failed", "service_unavailable", "model_missing", "capability_probe_failed"}:
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
        "service_unavailable": "本地服务未连通",
        "model_missing": "服务在线，模型未安装",
        "disabled": "已关闭",
    }
    return status_labels.get(status, status)


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
