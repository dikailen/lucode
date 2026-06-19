from __future__ import annotations

from lucode.gui.sidebar_data import load_default_mcp_rows, load_default_skill_cards


def test_load_default_skill_cards_contains_workbench_skills():
    cards = load_default_skill_cards()
    titles = [card.title for card in cards]

    assert titles[:6] == [
        "代码工程",
        "项目探索",
        "最终汇总",
        "技能创建",
        "单脑执行",
        "串行执行",
    ]
    assert all("本地" in card.chips for card in cards[:6])


def test_load_default_mcp_rows_contains_core_and_image_draw_status():
    rows = load_default_mcp_rows()
    by_id = {row.id: row for row in rows}

    assert by_id["filesystem"].status == "已连接"
    assert by_id["git"].status == "已连接"
    assert by_id["browser"].status in {"已连接", "可用"}
    assert by_id["image_draw"].status == "离线"
