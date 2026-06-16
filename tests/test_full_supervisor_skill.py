from __future__ import annotations

from catalog_system.refresher import build_skill_catalog
from skills.loader import PROJECT_ROOT, load_skill


def _skill(skill_id: str) -> dict | None:
    for item in build_skill_catalog(PROJECT_ROOT, use_cache=False).get("skills", []):
        if item.get("id") == skill_id:
            return item
    return None


def test_full_supervisor_registered_as_internal_non_assignable():
    item = _skill("full_supervisor")

    assert item is not None
    assert item.get("internal") is True
    assert item.get("assignable") is False
    assert item.get("selectable") is False
    assert item.get("planner_visible") is False


def test_full_supervisor_skill_loads_as_prompt():
    text = load_skill("full_supervisor")

    assert "主管" in text
    assert "--- SKILL START ---" in text
