from __future__ import annotations

from lucode.gui.chat_session import GuiChatSession
from lucode.gui.control_panel import (
    execution_mode_options,
    privacy_mode_options,
    query_refiner_available_for_mode,
    role_options,
    roles_for_mode,
)


def test_execution_mode_options_cover_known_modes():
    keys = [key for key, _label in execution_mode_options()]
    assert keys == ["solo", "serial", "full"]
    assert all(label for _key, label in execution_mode_options())


def test_privacy_mode_options_cover_known_modes():
    keys = [key for key, _label in privacy_mode_options()]
    assert keys == ["offline", "local_first", "cloud_allowed"]


def test_role_options_follow_role_order():
    keys = [key for key, _label in role_options()]
    assert keys == ["query_refiner", "orchestrator", "executor", "final_synthesizer"]


def test_solo_mode_uses_only_executor():
    rows = roles_for_mode("solo")
    assert [r for r, _u in rows] == ["executor"]
    assert all(u == "always" for _r, u in rows)


def test_serial_and_full_use_planner_executor_and_conditional_synthesizer():
    for mode in ("serial", "full"):
        rows = roles_for_mode(mode)
        roles = [r for r, _u in rows]
        assert roles == ["orchestrator", "executor", "final_synthesizer"]
        usage = dict(rows)
        assert usage["orchestrator"] == "always"
        assert usage["executor"] == "always"
        assert usage["final_synthesizer"] == "conditional"


def test_unknown_mode_falls_back_to_solo_roles():
    assert [r for r, _u in roles_for_mode("bogus")] == ["executor"]


def test_query_refiner_unavailable_in_solo_only():
    assert query_refiner_available_for_mode("solo") is False
    assert query_refiner_available_for_mode("serial") is True
    assert query_refiner_available_for_mode("full") is True


def test_set_query_refiner_enabled_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_query_refiner_enabled(True) is True
    assert session.settings.query_refiner_enabled is True
    assert session.set_query_refiner_enabled(False) is False
    assert session.settings.query_refiner_enabled is False


def test_set_execution_mode_normalizes_and_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_execution_mode("FULL") == "full"
    assert session.settings.execution_mode == "full"
    # unknown falls back to solo default
    assert session.set_execution_mode("bogus") == "solo"
    assert session.settings.execution_mode == "solo"


def test_set_privacy_mode_normalizes_and_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_privacy_mode("cloud_allowed") == "cloud_allowed"
    assert session.settings.privacy_mode == "cloud_allowed"
    assert session.set_privacy_mode("nonsense") == "local_first"
    assert session.settings.privacy_mode == "local_first"


def test_set_model_for_role_promotes_to_front(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.executor_model_priority = ["a", "b", "c"]
    result = session.set_model_for_role("executor", "c")
    assert result[0] == "c"
    assert result == ["c", "a", "b"]
    assert session.settings.executor_model_priority == ["c", "a", "b"]


def test_set_model_for_role_accepts_role_alias(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.orchestrator_model_priority = ["x"]
    result = session.set_model_for_role("主脑", "y")
    assert result == ["y", "x"]
    assert session.settings.orchestrator_model_priority == ["y", "x"]


def test_set_model_for_role_empty_id_is_noop_on_priority_order(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.executor_model_priority = ["a", "b"]
    result = session.set_model_for_role("executor", "")
    assert result == ["a", "b"]
