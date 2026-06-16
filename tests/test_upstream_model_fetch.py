from __future__ import annotations

import requests

from catalog_system.model_probe import fetch_upstream_models


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class _FakeSession:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.trust_env = True
        self.closed = False
        self.calls = []

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response

    def close(self):
        self.closed = True


def test_fetch_upstream_models_parses_openai_compatible_response(monkeypatch):
    session = _FakeSession(
        _FakeResponse(
            200,
            {
                "data": [
                    {"id": "deepseek-chat"},
                    {"id": "deepseek-reasoner"},
                    {"id": ""},
                    {"object": "model"},
                    {"id": "deepseek-chat"},
                ]
            },
        )
    )
    monkeypatch.setattr(requests, "Session", lambda: session)

    result = fetch_upstream_models("https://api.deepseek.com/v1", "sk-test")

    assert result["ok"] is True
    assert result["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert result["source"] == "upstream"
    assert session.trust_env is False
    assert session.closed is True
    assert session.calls == [
        (
            "https://api.deepseek.com/v1/models",
            {
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer sk-test",
                },
                "timeout": 5.0,
            },
        )
    ]


def test_fetch_upstream_models_reports_auth_failure_without_key_leak(monkeypatch):
    session = _FakeSession(_FakeResponse(401, {"error": {"message": "bad key sk-secret"}}))
    monkeypatch.setattr(requests, "Session", lambda: session)

    result = fetch_upstream_models("https://proxy.example/v1", "sk-secret")

    assert result["ok"] is False
    assert result["models"] == []
    assert "鉴权失败" in result["error"]
    assert "sk-secret" not in result["error"]


def test_fetch_upstream_models_reports_network_failure(monkeypatch):
    session = _FakeSession(exc=requests.ConnectionError("offline"))
    monkeypatch.setattr(requests, "Session", lambda: session)

    result = fetch_upstream_models("https://proxy.example/v1", "sk-test")

    assert result["ok"] is False
    assert result["models"] == []
    assert "网络" in result["error"]
    assert session.closed is True


def test_fetch_upstream_models_uses_ollama_tags_endpoint(monkeypatch):
    session = _FakeSession(
        _FakeResponse(
            200,
            {
                "models": [
                    {"name": "llama3.1:8b"},
                    {"model": "qwen2.5:7b"},
                    {"name": "llama3.1:8b"},
                ]
            },
        )
    )
    monkeypatch.setattr(requests, "Session", lambda: session)

    result = fetch_upstream_models("http://localhost:11434", "", backend_type="ollama", timeout=1.5)

    assert result["ok"] is True
    assert result["models"] == ["llama3.1:8b", "qwen2.5:7b"]
    assert result["source"] == "ollama"
    assert session.calls[0][0] == "http://localhost:11434/api/tags"
    assert session.calls[0][1]["timeout"] == 1.5
