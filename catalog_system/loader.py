import json
from pathlib import Path

from catalog_system.refresher import build_skill_catalog
from runtime.config.app_home import get_app_home


PROJECT_ROOT = get_app_home()
CATALOG_DIR = PROJECT_ROOT / "catalogs"


def load_catalog(name: str) -> dict:
    """Load a JSON catalog from the local catalogs directory."""

    path = CATALOG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Catalog not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def load_skill_catalog() -> dict:
    """Load the runtime skill catalog with user and workspace extensions merged in."""

    return build_skill_catalog(PROJECT_ROOT, include_dynamic=True)


def load_mcp_catalog() -> dict:
    return load_catalog("mcp_catalog.json")


def load_permission_policy() -> dict:
    return load_catalog("permission_policy.json")


def compact_skill_catalog_for_prompt(catalog: dict | None = None) -> str:
    """Return a compact skill library for the planner prompt."""

    catalog = catalog or load_skill_catalog()
    lines = ["Skill 图书馆（只列主脑决策需要的信息）："]
    for item in catalog.get("skills", []):
        if not item.get("planner_visible"):
            continue
        assignable = bool(item.get("assignable", item.get("selectable", True)))
        borrowable = bool(item.get("borrowable", item.get("planner_visible", False)))
        status = "可执行" if assignable else "仅规则借阅" if borrowable else "不可执行"
        limits = "不能作为 task.skill_id 派给员工 Agent" if borrowable and not assignable else (
            ",".join(item.get("not_for") or []) or "无"
        )

        lines.append(
            "- "
            f"{item['id']} | "
            f"{status} | "
            f"模型:{item.get('default_model', 'unknown')} | "
            f"MCP:{','.join(item.get('allowed_mcp') or []) or '无'} | "
            f"用途:{','.join(item.get('good_for') or []) or _short(item.get('summary_zh', ''))} | "
            f"禁用:{limits}"
        )
    return "\n".join(lines)


def compact_cli_safety_rules_for_prompt() -> str:
    """Return the CLI safety skill rules the planner must know before routing commands."""

    skill_file = PROJECT_ROOT / "skills" / "cli-command-safety" / "SKILL.md"
    try:
        text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return (
            "CLI 安全认知：cli-command-safety 未找到。"
            "主脑规划 command_runner 时仍必须遵守权限策略和 CommandAnalyzer。"
        )

    body = _strip_frontmatter(text).strip()
    if not body:
        return ""
    return (
        "主脑在规划 command_runner、native fast path 或未来沙箱命令前，"
        "必须先借阅 cli_command_safety。执行层仍由 CommandAnalyzer 最终执法。\n"
        + _short(body, limit=1400)
    )


def compact_mcp_catalog_for_prompt() -> str:
    """Return a compact MCP library for the planner prompt."""

    catalog = load_mcp_catalog()
    lines = ["MCP 图书馆："]
    for item in catalog.get("mcp_servers", []):
        lines.append(
            "- "
            f"{item['id']} | "
            f"工具:{','.join(item.get('tools') or [])} | "
            f"授权:{','.join(item.get('allowed_for_skills') or []) or '无'} | "
            f"风险:{item.get('risk_level', 'unknown')} | "
            f"审批:{'需要' if item.get('approval_required') else '不需要'} | "
            f"用途:{_short(item.get('summary_zh', ''))}"
        )
    return "\n".join(lines)


def compact_permission_policy_for_prompt() -> str:
    """Return a compact permission policy for the planner prompt."""

    catalog = load_permission_policy()
    lines = ["权限策略："]
    for name, item in catalog.get("permissions", {}).items():
        lines.append(
            "- "
            f"{name} | 默认:{item.get('default')} | "
            f"范围:{item.get('scope')} | "
            f"说明:{_short(item.get('notes', ''))}"
        )
    hard_denies = catalog.get("hard_denies") or []
    if hard_denies:
        lines.append("硬性拒绝：" + "；".join(hard_denies))
    return "\n".join(lines)


def compact_catalog_for_prompt(catalog: dict) -> str:
    """Convert arbitrary catalog JSON into compact stable text."""

    return json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))


def _short(value: str, limit: int = 90) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :]
