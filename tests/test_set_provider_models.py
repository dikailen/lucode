from __future__ import annotations

import pytest

from runtime.config.model_config import (
    connect_provider,
    load_auth,
    load_lucode_config,
    load_provider_catalog,
    set_provider_models,
)


def _deepseek_endpoints() -> tuple[str, str]:
    preset = load_provider_catalog().get("deepseek") or {}
    return str(preset.get("homepage") or "https://deepseek.example"), str(
        preset.get("base_url") or "https://deepseek.example/v1"
    )


def _connect_deepseek(ws, uh, models):
    homepage, base_url = _deepseek_endpoints()
    connect_provider(
        "deepseek",
        api_key="sk-test",
        workspace_root=ws,
        user_home=uh,
        homepage=homepage,
        base_url=base_url,
        models=models,
    )


def test_set_provider_models_replaces_models_without_touching_key(tmp_path):
    ws = tmp_path / "ws"
    uh = tmp_path / "home"
    _connect_deepseek(ws, uh, ["deepseek-chat", "deepseek-reasoner"])

    result = set_provider_models("deepseek", ["deepseek-v3", "deepseek-v3", " deepseek-r1 "], workspace_root=ws)

    config = load_lucode_config(workspace_root=ws)
    auth = load_auth(user_home=uh)
    assert config["provider"]["deepseek"]["models"] == ["deepseek-v3", "deepseek-r1"]
    assert auth["providers"]["deepseek"]["api_key"] == "sk-test"
    assert result["provider_id"] == "deepseek"
    assert result["models"] == ["deepseek-v3", "deepseek-r1"]
    assert result["changed"] is True


def test_set_provider_models_prunes_removed_role_refs(tmp_path):
    ws = tmp_path / "ws"
    uh = tmp_path / "home"
    _connect_deepseek(ws, uh, ["deepseek-chat", "deepseek-reasoner"])
    config = load_lucode_config(workspace_root=ws)
    config["model"] = {
        "primary": "deepseek/deepseek-chat",
        "fallback": ["deepseek/deepseek-reasoner"],
    }
    config["roles"] = {
        "executor": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
        "orchestrator": ["deepseek/deepseek-chat"],
    }
    from runtime.config.model_config import save_lucode_config

    save_lucode_config(config, workspace_root=ws)

    result = set_provider_models("deepseek", ["deepseek-reasoner"], workspace_root=ws)

    config = load_lucode_config(workspace_root=ws)
    assert config["provider"]["deepseek"]["models"] == ["deepseek-reasoner"]
    assert config["model"] == {
        "primary": "deepseek/deepseek-reasoner",
        "fallback": [],
    }
    assert config["roles"] == {"executor": ["deepseek/deepseek-reasoner"]}
    assert result["removed_model_refs"] == 3
    assert result["removed_roles"] == ["orchestrator"]


def test_set_provider_models_rejects_empty_list(tmp_path):
    ws = tmp_path / "ws"
    uh = tmp_path / "home"
    _connect_deepseek(ws, uh, ["deepseek-chat"])

    with pytest.raises(ValueError, match="至少保留一个模型"):
        set_provider_models("deepseek", [], workspace_root=ws)

    assert load_lucode_config(workspace_root=ws)["provider"]["deepseek"]["models"] == ["deepseek-chat"]


def test_set_provider_models_rejects_missing_provider(tmp_path):
    with pytest.raises(ValueError, match="未找到 Provider"):
        set_provider_models("missing", ["model-a"], workspace_root=tmp_path / "ws")
