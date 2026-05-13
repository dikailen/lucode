from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills.registry import SKILLS


PROTECTED_SYSTEM_SKILLS = {
    "task_router",
    "query_refiner",
    "orchestrator_planner",
    "final_synthesizer",
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
        "core": _discover_skills_in_dir(roots.app_home / "skills", "core"),
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
    for title, key in [("内置核心", "core"), ("用户全局", "user"), ("当前项目", "workspace")]:
        lines.append("")
        lines.append(title)
        items = layers.get(key) or []
        lines.extend(_render_skill_items(items) if items else ["- 无"])
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
        display_name = meta.get("name") or folder
        summary = meta.get("description") or ""
        item = {
            "id": skill_id,
            "folder": folder,
            "display_name_zh": display_name,
            "summary_zh": summary,
            "source": source,
            "path": str(skill_file.parent),
            "internal": skill_id in PROTECTED_SYSTEM_SKILLS,
            "selectable": skill_id not in PROTECTED_SYSTEM_SKILLS,
            "blocked": False,
            "status": "可用",
        }
        items.append(item)
    return items


def _mark_skill_safety(item: dict[str, Any]) -> dict[str, Any]:
    marked = dict(item)
    if marked.get("id") in PROTECTED_SYSTEM_SKILLS:
        marked["blocked"] = True
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
        summary = item.get("summary_zh") or "无说明"
        lines.append(f"- {item.get('id')} | {item.get('display_name_zh')} | {status}")
        lines.append(f"  说明：{summary}")
        lines.append(f"  来源：{_source_label(item.get('source'))}")
    return lines


def _render_mcp_items(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in items:
        trusted = "已信任" if item.get("trusted") else "未信任"
        enabled = "已启用" if item.get("enabled") else "未启用"
        tools = ", ".join(item.get("tools") or []) or "未声明"
        lines.append(
            f"- {item.get('id')} | {item.get('display_name_zh')} | "
            f"{trusted} | {enabled} | 风险 {item.get('risk_level') or 'unknown'}"
        )
        lines.append(f"  工具：{tools}")
        prompts = _mcp_prompt_names(item.get("prompts"))
        if prompts:
            lines.append(f"  Prompts：{', '.join(prompts)}")
        lines.append(f"  来源：{_source_label(item.get('source'))}")
    return lines


def _source_label(source: str | None) -> str:
    return {
        "core": "内置核心",
        "user": "用户全局",
        "workspace": "当前项目",
    }.get(str(source or ""), str(source or "未知"))


def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    meta: dict[str, str] = {}
    for raw_line in text[3:end].splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


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
