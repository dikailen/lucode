from pathlib import Path

from catalog_system.refresher import build_skill_catalog
from runtime.common.text_utils import sanitize_text
from skills.registry import SKILLS


SKILLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILLS_DIR.parent


def load_skill(skill_name: str) -> str:
    """Load a skill's SKILL.md as an Agent instruction prompt."""

    skill_file = _skill_file_for(skill_name)
    if skill_file is None:
        raise KeyError(f"Unknown skill: {skill_name}")

    if not skill_file.exists():
        raise FileNotFoundError(f"Skill file not found: {skill_file}")

    skill_text = sanitize_text(skill_file.read_text(encoding="utf-8"))

    return f"""
You are a specialist Agent using the following skill instructions.
Apply the skill directly to the user's request.
Do not describe which Agent should handle the task.
Do not output routing JSON or internal handoff instructions.
Return the final answer to the user in Chinese unless the user asks otherwise.
If the user asks to delete or remove files, explain the target and reason first.
Then use the safe-delete tool only to create a zip backup if it is available.
The safe-delete tool does not move or delete the original file.
Never present unsafe deletion commands.

--- SKILL START ---
{skill_text}
--- SKILL END ---
""".strip()


def skill_description(skill_name: str) -> str:
    """Return a concise handoff description for a skill-backed Agent."""

    item = _catalog_item_for(skill_name)
    if item is not None:
        return item.get("summary_zh") or item.get("description") or item.get("display_name_zh") or skill_name

    if skill_name in SKILLS:
        return SKILLS[skill_name]["description"]

    raise KeyError(f"Unknown skill: {skill_name}")


def skill_runtime_metadata(skill_name: str) -> dict:
    """Return the resolved skill metadata used for execution prompts."""

    item = _catalog_item_for(skill_name)
    if item is not None:
        return {
            "id": item.get("id") or skill_name,
            "source": item.get("source") or "registry",
            "summary": item.get("summary_zh") or item.get("description") or item.get("display_name_zh") or "",
            "path": item.get("path") or "",
        }
    if skill_name in SKILLS:
        return {
            "id": skill_name,
            "source": "registry",
            "summary": SKILLS[skill_name].get("description") or "",
            "path": SKILLS[skill_name].get("folder") or "",
        }
    raise KeyError(f"Unknown skill: {skill_name}")


def _skill_file_for(skill_name: str) -> Path | None:
    item = _catalog_item_for(skill_name)
    if item is not None:
        skill_file = _resolve_catalog_skill_file(item)
        if skill_file is not None:
            return skill_file

    if skill_name in SKILLS:
        folder = SKILLS[skill_name]["folder"]
        return (SKILLS_DIR / folder / "SKILL.md").resolve()

    return None


def _catalog_item_for(skill_name: str) -> dict | None:
    catalog = build_skill_catalog(PROJECT_ROOT)
    matches = []
    for item in catalog.get("skills", []):
        if item.get("id") == skill_name:
            matches.append(item)
    if not matches:
        return None
    return _preferred_catalog_item(matches)


def _preferred_catalog_item(items: list[dict]) -> dict:
    priority = {"workspace": 0, "user": 1, "core": 2, "sample": 3}
    return sorted(items, key=lambda item: priority.get(str(item.get("source") or ""), 9))[0]


def _resolve_catalog_skill_file(item: dict) -> Path | None:
    folder = str(item.get("folder") or "").strip()
    source = str(item.get("source") or "").strip()
    catalog_path = str(item.get("path") or "").strip()
    if not folder and not catalog_path:
        return None

    roots = _allowed_skill_roots()
    if source == "core":
        base = roots["app"]
        allowed_root = roots["app"] / "core_skills"
        candidate = _resolve_relative_catalog_path(base, catalog_path, default=f"core_skills/{folder}")
    elif source == "sample":
        base = roots["app"]
        allowed_root = roots["app"] / "skills"
        candidate = _resolve_relative_catalog_path(base, catalog_path, default=f"skills/{folder}")
    elif source == "user":
        base = roots["user"]
        allowed_root = roots["user"] / "skills"
        candidate = _resolve_relative_catalog_path(base, catalog_path, default=f"skills/{folder}")
    elif source == "workspace":
        base = roots["workspace"]
        allowed_root = roots["workspace"] / ".lucode" / "skills"
        candidate = _resolve_relative_catalog_path(base, catalog_path, default=f".lucode/skills/{folder}")
    else:
        return None

    if candidate is None:
        return None
    if not _is_within_root(candidate.parent, allowed_root):
        return None
    return candidate


def _resolve_relative_catalog_path(base: Path, catalog_path: str, *, default: str) -> Path | None:
    relative = catalog_path or default
    relative_path = Path(relative)
    if relative_path.is_absolute():
        return None
    return (base / relative_path / "SKILL.md").resolve()


def _allowed_skill_roots() -> dict[str, Path]:
    import os

    return {
        "app": PROJECT_ROOT.resolve(),
        "user": Path(os.environ.get("LUCODE_USER_HOME") or Path.home() / ".lucode").resolve(),
        "workspace": Path(os.environ.get("LUCODE_WORKSPACE_ROOT") or Path.cwd()).resolve(),
    }


def _is_within_root(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    root = Path(root).resolve()
    return resolved == root or root in resolved.parents
