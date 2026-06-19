from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillCard:
    id: str
    title: str
    description: str
    chips: tuple[str, ...]


@dataclass(frozen=True)
class McpRow:
    id: str
    title: str
    status: str
    detail: str = ""


SKILL_ORDER = (
    "code_engineer",
    "project_explorer",
    "final_synthesizer",
    "skill_creator",
    "solo_executor_contract",
    "serial_executor_contract",
)

SKILL_TITLES = {
    "code_engineer": "Code Engineer",
    "project_explorer": "Project Explorer",
    "final_synthesizer": "Final Synthesizer",
    "skill_creator": "Skill Creator",
    "solo_executor_contract": "Solo Executor",
    "serial_executor_contract": "Serial Executor",
}

MCP_ALIASES = {
    "filesystem": ("project_filesystem_readonly", "skills_filesystem_readonly"),
    "git": ("git_tools",),
    "browser": ("web_search",),
    "image_draw": ("image_draw",),
}


def load_default_skill_cards() -> list[SkillCard]:
    registry = _load_skill_registry()
    cards: list[SkillCard] = []
    for skill_id in SKILL_ORDER:
        metadata = registry.get(skill_id, {})
        cards.append(
            SkillCard(
                id=skill_id,
                title=SKILL_TITLES.get(skill_id, _title_from_id(skill_id)),
                description=str(metadata.get("description") or ""),
                chips=_skill_chips(skill_id),
            )
        )
    return cards


def load_default_mcp_rows() -> list[McpRow]:
    catalog = _load_mcp_catalog()
    rows: list[McpRow] = []
    for title, aliases in MCP_ALIASES.items():
        matches = [catalog[alias] for alias in aliases if alias in catalog]
        implemented = any(bool(item.get("implemented")) for item in matches)
        if matches:
            status = "Connected" if implemented else "Available"
            detail = _mcp_detail(matches[0])
        else:
            status = "Offline"
            detail = "Not configured"
        rows.append(McpRow(id=title, title=title, status=status, detail=detail))
    return rows


def _load_skill_registry() -> dict[str, dict[str, Any]]:
    try:
        from skills.registry import SKILLS

        return dict(SKILLS)
    except Exception:
        return {}


def _load_mcp_catalog() -> dict[str, dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / "catalogs" / "mcp_catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("mcp_servers") if isinstance(data, dict) else []
    if not isinstance(servers, list):
        return {}
    return {
        str(item.get("id") or ""): item
        for item in servers
        if isinstance(item, dict) and str(item.get("id") or "")
    }


def _skill_chips(skill_id: str) -> tuple[str, ...]:
    if skill_id in {"code_engineer", "project_explorer", "final_synthesizer"}:
        return ("Core", "Enabled", "Local")
    if skill_id == "skill_creator":
        return ("Core", "Local")
    return ("Enabled", "Local")


def _title_from_id(value: str) -> str:
    return " ".join(part.capitalize() for part in str(value or "").replace("-", "_").split("_") if part)


def _mcp_detail(item: dict[str, Any]) -> str:
    tools = item.get("tools")
    if isinstance(tools, list) and tools:
        return f"{len(tools)} tools"
    risk = str(item.get("risk_level") or "").strip()
    return risk or ""
