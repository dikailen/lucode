from pathlib import Path

from skills.registry import SKILLS


SKILLS_DIR = Path(__file__).resolve().parent


def load_skill(skill_name: str) -> str:
    """Load a skill's SKILL.md as an Agent instruction prompt."""

    if skill_name not in SKILLS:
        raise KeyError(f"Unknown skill: {skill_name}")

    skill = SKILLS[skill_name]
    skill_path = SKILLS_DIR / skill["folder"] / "SKILL.md"

    if not skill_path.exists():
        raise FileNotFoundError(f"Skill file not found: {skill_path}")

    skill_text = skill_path.read_text(encoding="utf-8")

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

    if skill_name not in SKILLS:
        raise KeyError(f"Unknown skill: {skill_name}")

    return SKILLS[skill_name]["description"]
