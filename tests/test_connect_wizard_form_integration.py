from __future__ import annotations

import asyncio

from lucode.shell import slash_commands
from runtime.config.connect_wizard import apply_connect_wizard_input, build_connect_wizard_state
from runtime.config.model_config import connect_provider, load_lucode_config, load_provider_catalog


class _WorkspaceContext:
    def __init__(self, workspace_root, user_home):
        self.workspace_root = workspace_root
        self.user_home = user_home
        self.app_home = workspace_root


def _state(tmp_path):
    return build_connect_wizard_state(_WorkspaceContext(tmp_path / "ws", tmp_path / "home"))


def _deepseek_endpoints() -> tuple[str, str]:
    preset = load_provider_catalog().get("deepseek") or {}
    return str(preset.get("homepage") or "https://deepseek.example"), str(
        preset.get("base_url") or "https://deepseek.example/v1"
    )


def test_inline_model_all_updates_form_primary_ref(monkeypatch, tmp_path):
    state, _ = apply_connect_wizard_input(_state(tmp_path), "provider deepseek")
    state, _ = apply_connect_wizard_input(state, "key sk-test")
    monkeypatch.setattr(
        "runtime.config.connect_wizard.fetch_upstream_models",
        lambda *args, **kwargs: {"ok": True, "models": ["m1", "m2"], "source": "upstream", "error": ""},
    )
    state, _ = apply_connect_wizard_input(state, "fetch")

    state, message, done = asyncio.run(
        slash_commands._apply_connect_form_action(
            console=None,
            state=state,
            action="model all",
            workspace_context=None,
            runtime_settings=None,
        )
    )

    assert done is False
    assert "字段已更新" in message
    assert state.selected_models == ["m1", "m2"]
    assert slash_commands._connect_state_primary_ref(state) == "deepseek/m1"
    assert slash_commands._selected_connect_model(state) == "m1"


def test_form_menu_exposes_fetch_and_apply_actions_for_provider_edit(tmp_path):
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
        models=["m1", "m2"],
    )
    state = build_connect_wizard_state(_WorkspaceContext(ws, uh), selected_provider="deepseek")

    commands = [item.command for item in slash_commands._connect_form_command_items(state)]

    assert "fetch" in commands
    assert "model all" in commands
    assert "apply" in commands
    assert "unselect all" in commands


def test_form_apply_updates_existing_provider_models(tmp_path):
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
        models=["m1", "m2"],
    )
    workspace_context = _WorkspaceContext(ws, uh)
    state = build_connect_wizard_state(workspace_context, selected_provider="deepseek")
    state, _ = apply_connect_wizard_input(state, "unselect m1")

    state, message, done = asyncio.run(
        slash_commands._apply_connect_form_action(
            console=None,
            state=state,
            action="apply",
            workspace_context=workspace_context,
            runtime_settings=None,
        )
    )

    assert done is False
    assert "已更新 Provider 模型列表" in message
    assert load_lucode_config(workspace_root=ws)["provider"]["deepseek"]["models"] == ["m2"]
