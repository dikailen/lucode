import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = PROJECT_ROOT / "catalogs"


def load_catalog(name: str) -> dict:
    """Load a JSON catalog from the local catalogs directory."""

    path = CATALOG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Catalog not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def load_skill_catalog() -> dict:
    return load_catalog("skill_catalog.json")


def load_mcp_catalog() -> dict:
    return load_catalog("mcp_catalog.json")


def load_permission_policy() -> dict:
    return load_catalog("permission_policy.json")


def compact_skill_catalog_for_prompt() -> str:
    """Return a compact skill library for the planner prompt."""

    catalog = load_skill_catalog()
    lines = ["Skill 图书馆（只列主脑决策需要的信息）："]
    for item in catalog.get("skills", []):
        if not item.get("selectable", True):
            lines.append(
                f"- {item['id']} | internal | 用途:{_short(item.get('summary_zh', ''))}"
            )
            continue

        lines.append(
            "- "
            f"{item['id']} | "
            f"模型:{item.get('default_model', 'unknown')} | "
            f"MCP:{','.join(item.get('allowed_mcp') or []) or '无'} | "
            f"用途:{','.join(item.get('good_for') or []) or _short(item.get('summary_zh', ''))} | "
            f"禁用:{','.join(item.get('not_for') or []) or '无'}"
        )
    return "\n".join(lines)


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
