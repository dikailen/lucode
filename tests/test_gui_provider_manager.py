from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from runtime.config.model_config import connect_provider, load_auth, load_lucode_config, load_provider_catalog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _deepseek_endpoints() -> tuple[str, str]:
    preset = load_provider_catalog().get("deepseek") or {}
    return str(preset.get("homepage") or "https://deepseek.example"), str(
        preset.get("base_url") or "https://deepseek.example/v1"
    )


def test_provider_manager_add_defaults_to_builtin_provider(tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    dialog = ProviderManagerDialog(workspace_root=tmp_path / "ws", user_home=tmp_path / "home")

    dialog.start_add()

    assert dialog._current_provider_id()


def test_provider_manager_add_fetches_models_without_auto_selecting(monkeypatch, tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    dialog = ProviderManagerDialog(workspace_root=tmp_path / "ws", user_home=tmp_path / "home")
    dialog.start_add()
    dialog.provider_combo.setCurrentText("DeepSeek")
    dialog.api_key_edit.setText("sk-test")

    def fake_fetch(base_url, api_key, backend_type="openai_compatible", timeout=5.0):
        assert api_key == "sk-test"
        assert "deepseek" in base_url
        return {"ok": True, "models": ["deepseek-chat", "deepseek-reasoner"], "source": "upstream", "error": ""}

    monkeypatch.setattr("lucode.gui.provider_manager.fetch_upstream_models", fake_fetch)

    dialog.fetch_models()

    assert dialog.api_key_edit.echoMode() == QLineEdit.Password
    assert dialog.model_names() == ["deepseek-chat", "deepseek-reasoner"]
    assert dialog.selected_models() == []
    assert dialog.save_current_provider() is False

    dialog.select_all_models.setChecked(True)
    assert dialog.selected_models() == ["deepseek-chat", "deepseek-reasoner"]

    dialog.save_current_provider()

    config = load_lucode_config(workspace_root=tmp_path / "ws")
    auth = load_auth(user_home=tmp_path / "home")
    assert config["provider"]["deepseek"]["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert auth["providers"]["deepseek"]["api_key"] == "sk-test"


def test_provider_manager_can_add_custom_proxy_provider(monkeypatch, tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    dialog = ProviderManagerDialog(workspace_root=tmp_path / "ws", user_home=tmp_path / "home")
    dialog.start_add()
    dialog.start_custom_provider("my_proxy")
    dialog.homepage_edit.setText("https://proxy.example.com")
    dialog.base_url_edit.setText("https://proxy.example.com/v1")
    dialog.api_key_edit.setText("sk-proxy")

    monkeypatch.setattr(
        "lucode.gui.provider_manager.fetch_upstream_models",
        lambda *args, **kwargs: {"ok": True, "models": ["proxy-chat", "proxy-code"], "source": "upstream", "error": ""},
    )

    dialog.fetch_models()
    dialog.set_model_checked("proxy-code", True)

    assert dialog.save_current_provider() is True
    config = load_lucode_config(workspace_root=tmp_path / "ws")
    auth = load_auth(user_home=tmp_path / "home")
    assert config["provider"]["my_proxy"]["base_url"] == "https://proxy.example.com/v1"
    assert config["provider"]["my_proxy"]["homepage"] == "https://proxy.example.com"
    assert config["provider"]["my_proxy"]["models"] == ["proxy-code"]
    assert auth["providers"]["my_proxy"]["api_key"] == "sk-proxy"


def test_provider_manager_blocks_fetch_in_offline_privacy_mode(monkeypatch, tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    dialog = ProviderManagerDialog(
        workspace_root=tmp_path / "ws",
        user_home=tmp_path / "home",
        privacy_mode="offline",
    )
    dialog.start_add()
    dialog.provider_combo.setCurrentText("DeepSeek")
    dialog.api_key_edit.setText("sk-test")

    def fail_fetch(*args, **kwargs):
        raise AssertionError("offline mode must not call upstream fetch")

    monkeypatch.setattr("lucode.gui.provider_manager.fetch_upstream_models", fail_fetch)

    dialog.fetch_models()

    assert "离线" in dialog.status_label.text() or "offline" in dialog.status_label.text().lower()
    assert dialog.model_names() == []


def test_provider_manager_edit_overwrites_models_without_touching_blank_key(tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    ws = tmp_path / "ws"
    home = tmp_path / "home"
    homepage, base_url = _deepseek_endpoints()
    connect_provider(
        "deepseek",
        api_key="sk-old",
        workspace_root=ws,
        user_home=home,
        homepage=homepage,
        base_url=base_url,
        models=["deepseek-chat", "deepseek-reasoner"],
    )

    dialog = ProviderManagerDialog(workspace_root=ws, user_home=home)
    dialog.start_edit("deepseek")

    assert dialog.provider_combo.isEnabled() is False
    assert dialog.model_names() == ["deepseek-chat", "deepseek-reasoner"]
    assert dialog.selected_models() == ["deepseek-chat", "deepseek-reasoner"]
    assert dialog.api_key_edit.text() == ""

    dialog.set_model_checked("deepseek-chat", False)
    dialog.save_current_provider()

    config = load_lucode_config(workspace_root=ws)
    auth = load_auth(user_home=home)
    assert config["provider"]["deepseek"]["models"] == ["deepseek-reasoner"]
    assert auth["providers"]["deepseek"]["api_key"] == "sk-old"


def test_provider_manager_delete_removes_provider_and_key(tmp_path, app):
    from lucode.gui.provider_manager import ProviderManagerDialog

    ws = tmp_path / "ws"
    home = tmp_path / "home"
    homepage, base_url = _deepseek_endpoints()
    connect_provider(
        "deepseek",
        api_key="sk-old",
        workspace_root=ws,
        user_home=home,
        homepage=homepage,
        base_url=base_url,
        models=["deepseek-chat"],
    )

    dialog = ProviderManagerDialog(workspace_root=ws, user_home=home)

    assert dialog.delete_provider("deepseek", confirm=True) is True
    assert "deepseek" not in load_lucode_config(workspace_root=ws).get("provider", {})
    assert "deepseek" not in load_auth(user_home=home).get("providers", {})


def test_control_bar_exposes_provider_manager_entry(app):
    from lucode.gui.control_panel import ControlBar

    bar = ControlBar()

    assert hasattr(bar, "provider_manager_requested")
    assert isinstance(bar.provider_manager_button, QPushButton)
    assert bar.provider_manager_button.text()
