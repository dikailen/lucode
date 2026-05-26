from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.config.skill_frontmatter import (
    frontmatter_bool,
    frontmatter_list,
    frontmatter_text,
    read_skill_frontmatter,
)
from skills.registry import SKILLS


PROTECTED_SYSTEM_SKILLS = {
    "lucode_native_capability",
    "task_router",
    "query_refiner",
    "orchestrator_planner",
    "final_synthesizer",
}
RULE_ONLY_SKILLS = {
    "cli_command_safety",
}


@dataclass(frozen=True)
class ExtensionRoots:
    app_home: Path
    user_home: Path
    workspace_root: Path


def extension_roots(workspace_context=None) -> ExtensionRoots:
    app_home = Path(
        getattr(workspace_context, "app_home", None) or os.environ.get("LUCODE_APP_HOME") or Path.cwd()
    ).resolve()
    user_home = Path(
        getattr(workspace_context, "user_home", None) or os.environ.get("LUCODE_USER_HOME") or Path.home() / ".lucode"
    ).resolve()
    workspace_root = Path(
        getattr(workspace_context, "workspace_root", None) or os.environ.get("LUCODE_WORKSPACE_ROOT") or Path.cwd()
    ).resolve()
    return ExtensionRoots(app_home=app_home, user_home=user_home, workspace_root=workspace_root)


def discover_skill_layers(workspace_context=None) -> dict[str, list[dict[str, Any]]]:
    roots = extension_roots(workspace_context)
    layers = {
        "core": _discover_skills_in_dir(roots.app_home / "core_skills", "core"),
        "sample": _discover_skills_in_dir(roots.app_home / "skills", "sample"),
        "user": _discover_skills_in_dir(roots.user_home / "skills", "user"),
        "workspace": _discover_skills_in_dir(roots.workspace_root / ".lucode" / "skills", "workspace"),
    }
    for source in ("user", "workspace"):
        layers[source] = [_mark_skill_safety(item) for item in layers[source]]
    return layers


def discover_mcp_layers(workspace_context=None) -> dict[str, list[dict[str, Any]]]:
    roots = extension_roots(workspace_context)
    return {
        "core": _discover_core_mcp(roots.app_home),
        "user": _discover_mcp_dir(roots.user_home / "mcp", "user"),
        "workspace": _discover_mcp_dir(roots.workspace_root / ".lucode" / "mcp", "workspace"),
    }


def render_workspace_skills(workspace_context=None) -> str:
    roots = extension_roots(workspace_context)
    items = discover_skill_layers(workspace_context)["workspace"]
    lines = [f"当前项目 Skills：{roots.workspace_root / '.lucode' / 'skills'}"]
    if not items:
        lines.append("- 无")
        lines.append("提示：在当前项目创建 .lucode/skills/<name>/SKILL.md 后可被发现。")
        return "\n".join(lines)
    lines.extend(_render_skill_items(items))
    lines.append("")
    lines.append("提示：核心系统 Skills 不允许被项目覆盖，可用 /skills_all 查看全部来源。")
    return "\n".join(lines)


def render_all_skills(workspace_context=None) -> str:
    layers = discover_skill_layers(workspace_context)
    lines = ["全部 Skills"]
    for title, key in [("内置核心", "core"), ("样例/测试", "sample"), ("用户全局", "user"), ("当前项目", "workspace")]:
        lines.append("")
        lines.append(title)
        items = layers.get(key) or []
        lines.extend(_render_skill_items(items) if items else ["- 无"])
    return "\n".join(lines)


def render_skill_detail(skill_name: str, workspace_context=None) -> str:
    normalized = _normalize_id(str(skill_name or "").lstrip("/"))
    if not normalized:
        return "请指定 Skill 名称，例如：/skill api-reviewer"
    layers = discover_skill_layers(workspace_context)
    matches = [
        item
        for items in layers.values()
        for item in items
        if normalized in {_normalize_id(item.get("id") or ""), _normalize_id(item.get("folder") or "")}
    ]
    if not matches:
        return f"没有找到 Skill：{skill_name}\n可用 /skills 查看当前项目，或 /skills_all 查看全部来源。"
    item = _preferred_skill_detail_item(matches)
    lines = [
        "Skill 详情",
        f"名称：{item.get('display_name_zh') or item.get('id')}",
        f"ID：{item.get('id')}",
        f"来源：{_source_label(item.get('source'))}",
        f"状态：{item.get('status') or ('已阻止：核心系统 Skill 不能被覆盖' if item.get('blocked') else '可用')}",
        f"说明：{item.get('summary_zh') or '无说明'}",
    ]
    allowed_tools = item.get("allowed_tools") or []
    trigger = item.get("trigger") or []
    if allowed_tools:
        lines.append(f"建议工具：{_join_compact(allowed_tools, limit=160)}")
    if trigger:
        lines.append(f"触发词：{_join_compact(trigger, limit=160)}")
    if item.get("argument_hint"):
        lines.append(f"参数提示：{item.get('argument_hint')}")
    if item.get("model"):
        lines.append(f"建议模型：{item.get('model')}")
    if item.get("disable_model_invocation"):
        lines.append("模型调用：关闭（只展开命令或说明）")
    lines.append(f"路径：{item.get('path') or '未知'}")
    return "\n".join(lines)


def render_workspace_mcp(workspace_context=None) -> str:
    roots = extension_roots(workspace_context)
    items = discover_mcp_layers(workspace_context)["workspace"]
    lines = [f"当前项目 MCP：{roots.workspace_root / '.lucode' / 'mcp'}"]
    if not items:
        lines.append("- 无")
        lines.append("提示：项目 MCP 默认不会自动运行，放入 .lucode/mcp/*.json 后先显示为未信任。")
        return "\n".join(lines)
    lines.extend(_render_mcp_items(items))
    lines.append("")
    lines.append("提示：项目 MCP 默认未信任、未启用；后续 trust/enable 前需要确认来源。")
    return "\n".join(lines)


def render_all_mcp(workspace_context=None) -> str:
    layers = discover_mcp_layers(workspace_context)
    lines = ["全部 MCP"]
    for title, key in [("内置核心", "core"), ("用户全局", "user"), ("当前项目", "workspace")]:
        lines.append("")
        lines.append(title)
        items = layers.get(key) or []
        lines.extend(_render_mcp_items(items) if items else ["- 无"])
    return "\n".join(lines)


def _discover_skills_in_dir(root: Path, source: str) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    folder_to_id = {meta.get("folder"): skill_id for skill_id, meta in SKILLS.items()}
    items: list[dict[str, Any]] = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        folder = skill_file.parent.name
        skill_id = folder_to_id.get(folder) or _normalize_id(folder)
        meta = _read_skill_frontmatter(skill_file)
        display_name = frontmatter_text(meta, "name") or folder
        summary = frontmatter_text(meta, "description")
        internal = skill_id in PROTECTED_SYSTEM_SKILLS or source == "core"
        borrowable = not internal
        assignable = borrowable and skill_id not in RULE_ONLY_SKILLS
        item = {
            "id": skill_id,
            "folder": folder,
            "display_name_zh": display_name,
            "summary_zh": summary,
            "allowed_tools": frontmatter_list(meta, "allowed-tools", "allowed_tools"),
            "trigger": frontmatter_list(meta, "trigger", "triggers"),
            "argument_hint": frontmatter_text(meta, "argument-hint", "argument_hint"),
            "model": frontmatter_text(meta, "model"),
            "disable_model_invocation": frontmatter_bool(
                meta,
                "disable-model-invocation",
                "disable_model_invocation",
            ),
            "source": source,
            "path": str(skill_file.parent),
            "internal": internal,
            "borrowable": borrowable,
            "assignable": assignable,
            "selectable": assignable,
            "blocked": False,
            "status": "仅规则借阅" if borrowable and not assignable else "可用",
        }
        items.append(item)
    return items


def _mark_skill_safety(item: dict[str, Any]) -> dict[str, Any]:
    marked = dict(item)
    if marked.get("id") in PROTECTED_SYSTEM_SKILLS:
        marked["blocked"] = True
        marked["borrowable"] = False
        marked["assignable"] = False
        marked["selectable"] = False
        marked["status"] = "已阻止：核心系统 Skill 不能被覆盖"
    return marked


def _discover_core_mcp(app_home: Path) -> list[dict[str, Any]]:
    catalog_path = app_home / "catalogs" / "mcp_catalog.json"
    if not catalog_path.exists():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = []
    for raw in data.get("mcp_servers", []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("id", _normalize_id(str(item.get("display_name_zh") or "mcp")))
        item.setdefault("display_name_zh", item["id"])
        item.setdefault("tools", [])
        item.setdefault("prompts", [])
        item["source"] = "core"
        item["trusted"] = True
        item["enabled"] = bool(item.get("implemented", True))
        item.setdefault("risk_level", "unknown")
        items.append(item)
    return items


def _discover_mcp_dir(root: Path, source: str) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        raw = _load_json_object(path)
        mcp_id = _normalize_id(str(raw.get("id") or path.stem))
        item = {
            "id": mcp_id,
            "display_name_zh": raw.get("display_name_zh") or raw.get("display_name") or raw.get("name") or mcp_id,
            "summary_zh": raw.get("summary_zh") or raw.get("summary") or "",
            "tools": _as_list(raw.get("tools") or []),
            "prompts": raw.get("prompts") or [],
            "source": source,
            "path": str(path),
            "risk_level": raw.get("risk_level") or "unknown",
            "trusted": bool(raw.get("trusted")) if source == "workspace" else raw.get("trusted", True) is not False,
            "enabled": bool(raw.get("enabled")) if source == "workspace" else raw.get("enabled", True) is not False,
            "approval_required": raw.get("approval_required", True),
        }
        if source == "workspace":
            item["trusted"] = bool(raw.get("trusted", False))
            item["enabled"] = bool(raw.get("enabled", False))
        items.append(item)
    return items


def _render_skill_items(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in items:
        status = item.get("status") or ("已阻止：核心系统 Skill 不能被覆盖" if item.get("blocked") else "可用")
        summary = _compact_text(item.get("summary_zh") or "无说明", limit=92)
        display_name = _compact_text(item.get("display_name_zh") or item.get("id") or "", limit=44)
        lines.append(f"- {item.get('id')} | {display_name} | {status}")
        lines.append(f"  说明：{summary}")
        lines.append(f"  来源：{_source_label(item.get('source'))}")
    return lines


def _render_mcp_items(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in items:
        trusted = "已信任" if item.get("trusted") else "未信任"
        enabled = "已启用" if item.get("enabled") else "未启用"
        tools = _join_compact(item.get("tools") or [], limit=92) or "未声明"
        lines.append(
            f"- {item.get('id')} | {item.get('display_name_zh')} | "
            f"{trusted} | {enabled} | 风险 {item.get('risk_level') or 'unknown'}"
        )
        lines.append(f"  工具：{tools}")
        prompts = _mcp_prompt_names(item.get("prompts"))
        if prompts:
            lines.append(f"  Prompts：{_join_compact(prompts, limit=92)}")
        lines.append(f"  来源：{_source_label(item.get('source'))}")
    return lines


def _join_compact(values, *, limit: int) -> str:
    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return ""
    joined = ", ".join(items)
    if len(joined) <= limit:
        return joined
    kept: list[str] = []
    used = 0
    for item in items:
        next_used = used + (2 if kept else 0) + len(item)
        if next_used > max(8, limit - 12):
            break
        kept.append(item)
        used = next_used
    remaining = len(items) - len(kept)
    if not kept:
        return _compact_text(items[0], limit=max(8, limit - 8)) + f"，另有 {remaining} 个"
    suffix = f"，另有 {remaining} 个" if remaining > 0 else ""
    return ", ".join(kept) + suffix


def _compact_text(value, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _source_label(source: str | None) -> str:
    return {
        "core": "内置核心",
        "sample": "样例/测试",
        "user": "用户全局",
        "workspace": "当前项目",
    }.get(str(source or ""), str(source or "未知"))


def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    return read_skill_frontmatter(skill_file)


def _preferred_skill_detail_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    order = {"workspace": 0, "user": 1, "core": 2, "sample": 3}
    return sorted(items, key=lambda item: order.get(str(item.get("source") or ""), 9))[0]


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]
    return []


def _mcp_prompt_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, raw in value.items():
            if isinstance(raw, dict):
                name = raw.get("name") or raw.get("id") or key
            else:
                name = key
            normalized = str(name or "").strip()
            if normalized:
                names.append(normalized)
        return names
    if isinstance(value, list):
        for raw in value:
            if isinstance(raw, dict):
                name = raw.get("name") or raw.get("id") or raw.get("title")
            else:
                name = raw
            normalized = str(name or "").strip()
            if normalized:
                names.append(normalized)
    return names


def _normalize_id(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_") or "unnamed"
