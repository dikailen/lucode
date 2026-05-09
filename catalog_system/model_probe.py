from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


CACHE_VERSION = 1
CACHE_RELATIVE_PATH = Path(".agent_cache") / "model_capabilities.json"
BASE_DIR = Path(__file__).resolve().parent.parent


def load_probe_cache(project_root: Path) -> dict:
    path = probe_cache_path(project_root)
    if not path.exists():
        return {"version": CACHE_VERSION, "results": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "results": {}}
    if data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "results": {}}
    data.setdefault("results", {})
    return data


def save_probe_cache(project_root: Path, cache: dict) -> None:
    path = probe_cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe_cache_path(project_root: Path) -> Path:
    return project_root / CACHE_RELATIVE_PATH


def cached_probe_for_model(project_root: Path, model_info: dict) -> dict | None:
    cache = load_probe_cache(project_root)
    entry = cache.get("results", {}).get(model_info.get("id") or "")
    if not entry:
        return None
    if entry.get("fingerprint") != model_fingerprint(model_info):
        return None
    return entry


def refresh_model_probe_cache(project_root: Path, model_catalog: dict, force: bool = False) -> dict:
    """Best-effort local model capability probing. Failures are cached, never raised."""

    if not _env_bool("MODEL_PROBE_ENABLED", True):
        return load_probe_cache(project_root)

    cache = load_probe_cache(project_root)
    results = cache.setdefault("results", {})
    timeout = _env_float("MODEL_PROBE_TIMEOUT_SECONDS", 2.0)
    service_timeout = _env_float("MODEL_SERVICE_PROBE_TIMEOUT_SECONDS", 1.0)
    ttl_seconds = _env_int("MODEL_PROBE_CACHE_TTL_SECONDS", 86400)
    failure_ttl_seconds = _env_int("MODEL_PROBE_FAILURE_TTL_SECONDS", 300)
    local_only = _env_bool("MODEL_PROBE_LOCAL_ONLY", True)

    for model in model_catalog.get("models", []):
        if not model.get("configured"):
            continue
        if local_only and not model.get("is_local"):
            continue

        probe_input = _probe_input_for_model(model)
        service_result = probe_model_service(probe_input, timeout=service_timeout)
        service_block_status = _service_block_status(service_result)
        fingerprint = model_fingerprint(model)

        if service_block_status:
            result = {
                "status": service_block_status,
                "supports_basic_chat": False,
                "supports_json_output": False,
                "supports_tools": False,
            }
            result.update(service_result)
            result.update(
                {
                    "model_id": model["id"],
                    "model_name": model.get("model_name") or "",
                    "backend_type": model.get("backend_type") or "",
                    "fingerprint": fingerprint,
                    "probed_at": time.time(),
                    "planner_suitable": False,
                    "execution_suitable": False,
                }
            )
            results[model["id"]] = result
            continue

        cached = results.get(model["id"])
        if cached and not force:
            age = time.time() - float(cached.get("probed_at") or 0)
            entry_ttl = failure_ttl_seconds if _failure_status(cached.get("status")) else ttl_seconds
            if (
                cached.get("fingerprint") == fingerprint
                and age < entry_ttl
                and cached.get("status") not in {"service_unavailable", "model_missing"}
            ):
                cached.update(service_result)
                results[model["id"]] = cached
                continue

        try:
            result = probe_model_capabilities(probe_input, timeout=timeout)
        except Exception as exc:
            status = "capability_probe_failed" if service_result.get("service_available") is True else "probe_failed"
            result = {
                "status": status,
                "error": str(exc),
                "supports_basic_chat": None,
                "supports_json_output": None,
                "supports_tools": None,
            }
        result.update(service_result)

        result.update(
            {
                "model_id": model["id"],
                "model_name": model.get("model_name") or "",
                "backend_type": model.get("backend_type") or "",
                "fingerprint": fingerprint,
                "probed_at": time.time(),
            }
        )
        result["planner_suitable"] = bool(result.get("supports_basic_chat"))
        result["execution_suitable"] = bool(result.get("supports_tools"))
        results[model["id"]] = result

    save_probe_cache(project_root, cache)
    return cache


def probe_model_service(model_info: dict, timeout: float = 1.0) -> dict:
    """Lightweight service health check that does not require model generation."""

    if model_info.get("backend_type") == "ollama":
        return probe_ollama_service(model_info, timeout=timeout)
    return {
        "service_status": "unknown",
        "service_available": None,
        "service_error": "",
        "model_present": None,
        "service_probed_at": time.time(),
    }


def probe_ollama_service(model_info: dict, timeout: float = 1.0) -> dict:
    endpoint = _ollama_tags_endpoint(model_info)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(endpoint, timeout=timeout)
    except requests.RequestException as exc:
        return {
            "service_status": "offline",
            "service_available": False,
            "service_error": str(exc),
            "model_present": None,
            "service_endpoint": endpoint,
            "service_probed_at": time.time(),
        }
    finally:
        session.close()

    if not (200 <= response.status_code < 300):
        return {
            "service_status": "unhealthy",
            "service_available": False,
            "service_error": _response_error_text(response) or f"HTTP {response.status_code}",
            "model_present": None,
            "service_endpoint": endpoint,
            "service_probed_at": time.time(),
        }

    model_names = _ollama_model_names(response)
    model_name = str(model_info.get("model_name") or "").strip()
    model_present = None
    if model_name and model_names:
        model_present = model_name in model_names

    return {
        "service_status": "online",
        "service_available": True,
        "service_error": "",
        "model_present": model_present,
        "installed_model_count": len(model_names),
        "service_endpoint": endpoint,
        "service_probed_at": time.time(),
    }


def probe_model_capabilities(model_info: dict, timeout: float = 2.0) -> dict:
    endpoint = _chat_completions_endpoint(model_info)
    headers = _headers(model_info)
    model_name = model_info.get("model_name") or ""

    basic = _post_json(
        endpoint,
        headers,
        {
            "model": model_name,
            "messages": [{"role": "user", "content": 'Return exactly this JSON: {"ok": true}'}],
            "stream": False,
        },
        timeout,
    )
    supports_basic_chat = 200 <= basic.status_code < 300
    supports_json_output = False
    basic_error = ""
    if supports_basic_chat:
        supports_json_output = _response_contains_json_ok(basic)
    else:
        basic_error = _response_error_text(basic)

    tool_payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Call the capability_probe tool with value ping."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "capability_probe",
                    "description": "Probe tool calling support.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "capability_probe"}},
        "stream": False,
    }
    tool_response = _post_json(endpoint, headers, tool_payload, timeout)
    supports_tools = False
    tool_error = ""
    if 200 <= tool_response.status_code < 300:
        supports_tools = _response_has_tool_call(tool_response)
        if not supports_tools:
            tool_error = "tool probe returned without tool_calls"
    else:
        tool_error = _response_error_text(tool_response)

    status = "ok" if supports_basic_chat else "chat_failed"
    if not supports_tools and _looks_like_tools_unsupported(tool_error):
        status = "tools_unsupported"

    return {
        "status": status,
        "supports_basic_chat": supports_basic_chat,
        "supports_json_output": supports_json_output,
        "supports_tools": supports_tools,
        "chat_error": basic_error,
        "tool_error": tool_error,
    }


def model_fingerprint(model_info: dict) -> str:
    text = "|".join(
        [
            str(model_info.get("id") or ""),
            str(model_info.get("backend_type") or ""),
            str(model_info.get("base_url") or model_info.get("base_url_configured") or ""),
            str(model_info.get("model_name") or ""),
        ]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chat_completions_endpoint(model_info: dict) -> str:
    base_url = str(model_info.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("model base_url is empty")
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    if model_info.get("backend_type") == "ollama":
        return f"{base_url}/v1/chat/completions"
    return f"{base_url}/chat/completions"


def _ollama_tags_endpoint(model_info: dict) -> str:
    base_url = str(model_info.get("base_url") or "").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    if not base_url:
        raise ValueError("model base_url is empty")
    return f"{base_url}/api/tags"


def _headers(model_info: dict) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = str(model_info.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _probe_input_for_model(model_info: dict) -> dict[str, Any]:
    env = {key: value for key, value in dotenv_values(BASE_DIR / ".env").items() if key}
    env.update({key: value for key, value in os.environ.items() if value is not None})
    model_id = str(model_info.get("id") or "")
    api_key = ""
    shared_group = str(model_info.get("shared_config_group") or "").strip().upper()
    if shared_group:
        if shared_group == "MIMO":
            api_key = env.get("MIMO_API_KEY") or env.get("MODEL_MIMO_API_KEY") or ""
        else:
            api_key = env.get(f"MODEL_{shared_group}_API_KEY") or ""
    elif model_id == "deepseek_V4_flash_model":
        api_key = env.get("DEEPSEEK_API_KEY") or ""
    elif model_id == "deepseek_V4_pro_model":
        api_key = env.get("DEEPSEEK_pro_API_KEY") or ""
    elif model_id == "mimo_model":
        api_key = env.get("MIMO_API_KEY") or ""
    elif model_id.endswith("_model"):
        prefix = model_id.removesuffix("_model").upper()
        api_key = env.get(f"MODEL_{prefix}_API_KEY") or ""

    return {
        "id": model_id,
        "model_name": model_info.get("model_name") or "",
        "backend_type": model_info.get("backend_type") or "",
        "base_url": model_info.get("base_url") or "",
        "api_key": api_key,
    }


def _post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(endpoint, headers=headers, json=payload, timeout=timeout)
    finally:
        session.close()


def _response_contains_json_ok(response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    content = _message_content(payload)
    if not content:
        return False
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return False
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return False
    return parsed.get("ok") is True


def _response_has_tool_call(response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    choices = payload.get("choices") or []
    if not choices:
        return False
    message = choices[0].get("message") or {}
    return bool(message.get("tool_calls"))


def _message_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _response_error_text(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return getattr(response, "text", "")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
    return str(payload)


def _ollama_model_names(response) -> set[str]:
    try:
        payload = response.json()
    except ValueError:
        return set()
    names = set()
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        for key in ["name", "model"]:
            value = str(item.get(key) or "").strip()
            if value:
                names.add(value)
    return names


def _service_block_status(service_result: dict) -> str:
    if service_result.get("service_available") is False:
        return "service_unavailable"
    if service_result.get("model_present") is False:
        return "model_missing"
    return ""


def _failure_status(status: str | None) -> bool:
    return str(status or "") in {
        "probe_failed",
        "chat_failed",
        "service_unavailable",
        "model_missing",
        "capability_probe_failed",
    }


def _looks_like_tools_unsupported(message: str) -> bool:
    text = str(message or "").lower()
    return "does not support tools" in text or "tools are not supported" in text or "tool" in text and "not support" in text


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default
