from __future__ import annotations

from runtime.config.connect_wizard import (
    apply_connect_wizard_input,
    build_connect_request_from_state,
    build_connect_wizard_state,
    render_connect_wizard_snapshot,
)
from runtime.config.model_config import connect_provider, load_lucode_config, load_provider_catalog


class _WorkspaceContext:
    def __init__(self, workspace_root, user_home):
        self.workspace_root = workspace_root
        self.user_home = user_home


def _state(tmp_path):
    return build_connect_wizard_state(
        _WorkspaceContext(tmp_path / "ws", tmp_path / "home"),
    )


def _deepseek_endpoints() -> tuple[str, str]:
    preset = load_provider_catalog().get("deepseek") or {}
    return str(preset.get("homepage") or "https://deepseek.example"), str(
        preset.get("base_url") or "https://deepseek.example/v1"
    )


def test_fetch_populates_available_models_without_auto_selecting(monkeypatch, tmp_path):
    state, _ = apply_connect_wizard_input(_state(tmp_path), "provider deepseek")
    state, _ = apply_connect_wizard_input(state, "key sk-test")

    def fake_fetch(base_url, api_key, backend_type="openai_compatible", timeout=5.0):
        return {"ok": True, "models": ["deepseek-chat", "deepseek-reasoner"], "source": "upstream", "error": ""}

    monkeypatch.setattr("runtime.config.connect_wizard.fetch_upstream_models", fake_fetch)

    state, message = apply_connect_wizard_input(state, "fetch")

    assert "已获取 2 个模型" in message
    assert state.available_models == ["deepseek-chat", "deepseek-reasoner"]
    assert state.selected_models == []
    assert build_connect_request_from_state(state).models == ("deepseek-chat",)
    snapshot = render_connect_wizard_snapshot(state)
    assert "可用模型：2 个（已选 0）" in snapshot
    assert "[1] ☐ deepseek-chat" in snapshot


def test_fetch_is_blocked_in_offline_privacy_mode(monkeypatch, tmp_path):
    state, _ = apply_connect_wizard_input(_state(tmp_path), "provider deepseek")
    state, _ = apply_connect_wizard_input(state, "key sk-test")
    state.privacy_mode = "offline"

    def fail_fetch(*args, **kwargs):
        raise AssertionError("offline mode must not call upstream fetch")

    monkeypatch.setattr("runtime.config.connect_wizard.fetch_upstream_models", fail_fetch)

    try:
        apply_connect_wizard_input(state, "fetch")
    except ValueError as exc:
        assert "离线" in str(exc) or "offline" in str(exc).lower()
    else:
        raise AssertionError("offline mode should reject fetch")


def test_model_all_selects_all_fetched_models(monkeypatch, tmp_path):
    state, _ = apply_connect_wizard_input(_state(tmp_path), "provider deepseek")
    state, _ = apply_connect_wizard_input(state, "key sk-test")
    monkeypatch.setattr(
        "runtime.config.connect_wizard.fetch_upstream_models",
        lambda *args, **kwargs: {"ok": True, "models": ["m1", "m2"], "source": "upstream", "error": ""},
    )

    state, _ = apply_connect_wizard_input(state, "fetch")
    state, message = apply_connect_wizard_input(state, "model all")
    request = build_connect_request_from_state(state)

    assert "已选择全部 2 个模型" in message
    assert state.selected_models == ["m1", "m2"]
    assert request.models == ("m1", "m2")


def test_model_number_and_name_toggle_fetched_models(monkeypatch, tmp_path):
    state, _ = apply_connect_wizard_input(_state(tmp_path), "provider deepseek")
    state, _ = apply_connect_wizard_input(state, "key sk-test")
    monkeypatch.setattr(
        "runtime.config.connect_wizard.fetch_upstream_models",
        lambda *args, **kwargs: {"ok": True, "models": ["m1", "m2", "m3"], "source": "upstream", "error": ""},
    )

    state, _ = apply_connect_wizard_input(state, "fetch")
    state, _ = apply_connect_wizard_input(state, "model 2")
    state, _ = apply_connect_wizard_input(state, "select m3")
    state, _ = apply_connect_wizard_input(state, "unselect m2")

    assert state.selected_models == ["m3"]
    assert build_connect_request_from_state(state).models == ("m3",)


def test_editing_existing_provider_can_apply_selected_models(tmp_path):
    ws = tmp_path / "ws"
    uh = tmp_path / "home"
    homepage, base_url = _deepseek_endpoints()
    connect_provider(
        "deepseek",
        api_key="sk-test",
        workspace_root=ws,
        user_home=uh,
        homepage=homepage,
        base_url=base_url,
        models=["deepseek-chat", "deepseek-reasoner"],
    )

    state = build_connect_wizard_state(_WorkspaceContext(ws, uh), selected_provider="deepseek")

    assert state.editing is True
    assert state.available_models == ["deepseek-chat", "deepseek-reasoner"]
    assert state.selected_models == ["deepseek-chat", "deepseek-reasoner"]

    state, _ = apply_connect_wizard_input(state, "unselect deepseek-chat")
    state, message = apply_connect_wizard_input(state, "apply")

    assert "已更新 Provider 模型列表" in message
    assert load_lucode_config(workspace_root=ws)["provider"]["deepseek"]["models"] == ["deepseek-reasoner"]
