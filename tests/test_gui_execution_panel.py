from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lucode.gui.widgets import WorkArea, action_label_from_event  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _plan_payload() -> dict:
    return {
        "route_type": "multi_agent",
        "tasks": [
            {
                "id": "task_1",
                "title": "Edit loader",
                "model": "deepseek",
                "mcp": ["filesystem"],
            }
        ],
    }


def test_tool_action_label_includes_tool_and_target_file():
    event = {
        "event_type": "ToolInvoked",
        "payload": {
            "tool": "edit_file",
            "files_touched": [{"path": "loader.py", "access": "write"}],
        },
    }

    assert action_label_from_event(event) == "Tool: edit_file(loader.py)"


def test_work_area_shows_worker_status_tool_and_delta(app):
    area = WorkArea(_plan_payload(), model_labels={"deepseek": "DeepSeek"})

    assert area.apply_event({"event_type": "TaskStarted", "task_id": "task_1"})
    assert area.apply_event(
        {
            "event_type": "ToolInvoked",
            "task_id": "task_1",
            "payload": {
                "tool": "edit_file",
                "files_touched": [{"path": "loader.py", "access": "write"}],
            },
        }
    )
    assert area.apply_event(
        {
            "event_type": "AgentMessageDelta",
            "task_id": "task_1",
            "payload": {"text": "patching loader"},
        }
    )
    app.processEvents()

    status_labels = [label.text() for label in area.findChildren(QLabel, "PlanStatus")]
    latest_labels = [label.text() for label in area.findChildren(QLabel, "NodeLatest")]
    detail_labels = [label.text() for label in area.findChildren(QLabel, "PlanActivity")]

    assert "Running" in status_labels
    assert any("patching loader" in text for text in latest_labels)
    assert any("Tool: edit_file(loader.py)" in text for text in detail_labels)
    assert any("patching loader" in text for text in detail_labels)


def test_worker_delta_is_consumed_by_work_area_not_answer():
    area = WorkArea(_plan_payload())

    consumed = area.apply_event(
        {
            "event_type": "AgentMessageDelta",
            "task_id": "task_1",
            "payload": {"text": "worker progress"},
        }
    )

    assert consumed
    assert area.worker_nodes["task_1"].latest_label.text() == "worker progress"
