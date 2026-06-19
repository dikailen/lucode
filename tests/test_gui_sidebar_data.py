from __future__ import annotations

from lucode.gui.sidebar_data import load_default_mcp_rows, load_default_skill_cards


def test_load_default_skill_cards_contains_workbench_skills():
    cards = load_default_skill_cards()
    titles = [card.title for card in cards]

    assert titles[:6] == [
        "Code Engineer",
        "Project Explorer",
        "Final Synthesizer",
        "Skill Creator",
        "Solo Executor",
        "Serial Executor",
    ]
    assert all("Local" in card.chips for card in cards[:6])


def test_load_default_mcp_rows_contains_core_and_image_draw_status():
    rows = load_default_mcp_rows()
    by_id = {row.id: row for row in rows}

    assert by_id["filesystem"].status == "Connected"
    assert by_id["git"].status == "Connected"
    assert by_id["browser"].status in {"Connected", "Available"}
    assert by_id["image_draw"].status == "Offline"
