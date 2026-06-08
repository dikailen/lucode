from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

CACHE_VERSION = 5
CACHE_RELATIVE_PATH = Path(".agent_cache") / "model_capabilities.json"


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


def refresh_model_probe_cache(
    project_root: Path,
    model_catalog: dict,
    force: bool = False,
    local_only: bool | None = None,
) -> dict:
    """Best-effort local model capability probing. Failures are cached, never raised."""

    if not _env_bool("MODEL_PROBE_ENABLED", True):
        return load_probe_cache(project_root)

    cache = load_probe_cache(project_root)
    results = cache.setdefault("results", {})
    timeout = _env_float("MODEL_PROBE_TIMEOUT_SECONDS", 2.0)
    service_timeout = _env_float("MODEL_SERVICE_PROBE_TIMEOUT_SECONDS", 1.0)
    ttl_seconds = _env_int("MODEL_PROBE_CACHE_TTL_SECONDS", 86400)
    failure_ttl_seconds = _env_int("MODEL_PROBE_FAILURE_TTL_SECONDS", 300)
    local_only = _env_bool("MODEL_PROBE_LOCAL_ONLY", True) if local_only is None else bool(local_only)

    for model in model_catalog.get("models", []):
        if not model.get("configured"):
            continue
        if local_only and not model.get("is_local"):
            continue

        probe_input = _probe_input_for_model(model)
        fingerprint = model_fingerprint(model)
        config_result = validate_probe_input(probe_input)
        if config_result.get("status") != "config_ok":
            result = {
                **config_result,
                "model_id": model["id"],
                "model_name": model.get("model_name") or "",
                "backend_type": model.get("backend_type") or "",
                "fingerprint": fingerprint,
                "probed_at": time.time(),
                "planner_suitable": False,
                "execution_suitable": False,
            }
            results[model["id"]] = result
            continue

        service_result = probe_model_service(probe_input, timeout=service_timeout)
        service_block_status = _service_block_status(service_result)

        if service_block_status:
            result = {
                "status": service_block_status,
                "supports_basic_chat": False,
                "supports_json_output": False,
                "supports_tools": False,
                "tools_api_accepted": False,
                "tools_auto_call": False,
                "tools_forced_choice": False,
                "tools_result_roundtrip": False,
                "supports_streaming": False,
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
                "tools_api_accepted": None,
                "tools_auto_call": None,
                "tools_forced_choice": None,
                "tools_result_roundtrip": None,
                "supports_streaming": None,
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
        roles = result.get("recommended_roles") or []
        result["planner_suitable"] = "orchestrator" in roles or bool(result.get("supports_basic_chat"))
        result["execution_suitable"] = "executor" in roles
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


def validate_probe_input(model_info: dict) -> dict:
    backend_type = str(model_info.get("backend_type") or "").strip()
    is_local = backend_type in {"ollama", "llama_cpp", "local"}
    missing = []
    if not str(model_info.get("base_url") or "").strip() and backend_type != "llama_cpp":
        missing.append("base_url")
    if not str(model_info.get("model_name") or "").strip():
        missing.append("model_name")
    if not is_local and not str(model_info.get("api_key") or "").strip():
        missing.append("api_key")
    if missing:
        return {
            "status": "config_incomplete",
            "missing": missing,
            "config_complete": False,
            "api_key_configured": bool(str(model_info.get("api_key") or "").strip()) or is_local,
            "base_url_configured": bool(str(model_info.get("base_url") or "").strip()),
            "model_name_configured": bool(str(model_info.get("model_name") or "").strip()),
            "supports_basic_chat": False,
            "supports_json_output": False,
            "supports_tools": False,
            "tools_api_accepted": False,
            "tools_auto_call": False,
            "tools_forced_choice": False,
            "tools_result_roundtrip": False,
            "supports_streaming": False,
        }
    return {
        "status": "config_ok",
        "missing": [],
        "config_complete": True,
        "api_key_configured": bool(str(model_info.get("api_key") or "").strip()) or is_local,
        "base_url_configured": bool(str(model_info.get("base_url") or "").strip()) or backend_type == "llama_cpp",
        "model_name_configured": True,
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
    profile = probe_profile_for_model(model_info)
    context_info = estimate_context_window(model_info, profile)
    chat_timeout = _probe_step_timeout("MODEL_PROBE_CHAT_TIMEOUT_SECONDS", max(timeout, 6.0), profile, "chat_timeout")
    tool_timeout = _probe_step_timeout("MODEL_PROBE_TOOL_TIMEOUT_SECONDS", timeout, profile, "tool_timeout")
    stream_timeout = _probe_step_timeout(
        "MODEL_PROBE_STREAM_TIMEOUT_SECONDS", timeout, profile, "stream_timeout"
    )
    endpoint = _chat_completions_endpoint(model_info)
    headers = _headers(model_info)
    model_name = model_info.get("model_name") or ""

    probe_started = time.perf_counter()
    basic, basic_exception, chat_latency_ms = _timed_post_json(
        endpoint,
        headers,
        {
            "model": model_name,
            "messages": [{"role": "user", "content": 'Return exactly this JSON: {"ok": true}'}],
            "stream": False,
        },
        chat_timeout,
    )
    supports_basic_chat = basic is not None and 200 <= basic.status_code < 300
    supports_json_output = False
    basic_error = ""
    if supports_basic_chat:
        supports_json_output = _response_contains_json_ok(basic)
    else:
        basic_error = str(basic_exception) if basic_exception else _response_error_text(basic)
        result = {
            "status": "chat_failed",
            "supports_basic_chat": False,
            "supports_json_output": False,
            "supports_tools": False,
            "tools_api_accepted": False,
            "tools_auto_call": False,
            "tools_forced_choice": False,
            "tools_result_roundtrip": False,
            "supports_streaming": False,
            "chat_error": basic_error,
            "tool_error": "",
            "tool_api_error": "",
            "auto_tool_error": "",
            "forced_tool_error": "",
            "tool_roundtrip_error": "",
            "stream_error": "",
            "probe_profile": profile["provider_id"],
            "probe_tool_choice_modes": profile["tool_choice_modes"],
            "latency_ms": _elapsed_ms(probe_started),
            "chat_latency_ms": chat_latency_ms,
            "tool_accept_latency_ms": 0,
            "auto_tool_latency_ms": 0,
            "forced_tool_latency_ms": 0,
            "tool_roundtrip_latency_ms": 0,
            "stream_latency_ms": 0,
            **context_info,
        }
        result["recommended_roles"] = recommend_model_roles(result)
        return result

    tools = [_capability_probe_tool_schema()]
    tool_accept_payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Reply with exactly: tools accepted"}],
        "tools": tools,
        "stream": False,
    }
    tool_accept_response, tool_accept_exception, tool_accept_latency_ms = _timed_post_json(
        endpoint, headers, tool_accept_payload, tool_timeout
    )
    tools_api_accepted = tool_accept_response is not None and 200 <= tool_accept_response.status_code < 300
    tool_api_error = ""
    if not tools_api_accepted:
        tool_api_error = str(tool_accept_exception) if tool_accept_exception else _response_error_text(tool_accept_response)
        if tool_accept_exception is not None:
            tools_api_accepted = None

    auto_response = None
    auto_exception = None
    if tools_api_accepted and "auto" in profile["tool_choice_modes"]:
        auto_response, auto_exception, auto_tool_latency_ms = _timed_post_json(
            endpoint,
            headers,
            _tool_probe_payload(model_name, tools, "auto", profile),
            tool_timeout,
        )
    else:
        auto_tool_latency_ms = 0
    tools_auto_call = False
    auto_tool_error = tool_api_error
    auto_tool_call = None
    if auto_response is not None:
        if 200 <= auto_response.status_code < 300:
            auto_tool_call = _first_tool_call(auto_response)
            tools_auto_call = auto_tool_call is not None
            auto_tool_error = "" if tools_auto_call else "auto tool probe returned without tool_calls"
        else:
            auto_tool_error = _response_error_text(auto_response)
    elif auto_exception is not None:
        auto_tool_error = str(auto_exception)

    forced_response = None
    forced_exception = None
    if tools_api_accepted and "forced" in profile["tool_choice_modes"]:
        forced_response, forced_exception, forced_tool_latency_ms = _timed_post_json(
            endpoint,
            headers,
            _tool_probe_payload(model_name, tools, "forced", profile),
            tool_timeout,
        )
    else:
        forced_tool_latency_ms = 0
    tools_forced_choice = False
    forced_tool_error = tool_api_error
    if forced_response is not None:
        if 200 <= forced_response.status_code < 300:
            tools_forced_choice = _response_has_tool_call(forced_response)
            forced_tool_error = "" if tools_forced_choice else "forced tool probe returned without tool_calls"
        else:
            forced_tool_error = _response_error_text(forced_response)
    elif forced_exception is not None:
        forced_tool_error = str(forced_exception)

    tools_result_roundtrip = False
    tool_roundtrip_error = ""
    if auto_tool_call:
        roundtrip_response, roundtrip_exception, tool_roundtrip_latency_ms = _timed_post_json(
            endpoint,
            headers,
            _tool_roundtrip_payload(model_name, auto_tool_call),
            tool_timeout,
        )
        if roundtrip_response is not None and 200 <= roundtrip_response.status_code < 300:
            tools_result_roundtrip = True
        else:
            tool_roundtrip_error = str(roundtrip_exception) if roundtrip_exception else _response_error_text(roundtrip_response)
    else:
        tool_roundtrip_latency_ms = 0

    tool_probe_completed = tool_accept_response is not None or bool(tool_api_error and tool_accept_exception is None)
    supports_tools = True if bool(tools_auto_call or tools_forced_choice or tools_result_roundtrip) else None
    if supports_tools is None and not tool_probe_completed:
        supports_tools = None
    elif supports_tools is None and not tools_api_accepted and _looks_like_tools_unsupported(tool_api_error):
        supports_tools = False
    tool_error = ""
    if not tools_api_accepted:
        tool_error = tool_api_error
    elif supports_tools is True:
        tool_error = forced_tool_error if not tools_forced_choice else ""
    else:
        tool_error = auto_tool_error or forced_tool_error

    stream_payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "stream": True,
    }
    stream_response, stream_exception, stream_latency_ms = _timed_post_json(
        endpoint, headers, stream_payload, stream_timeout
    )
    supports_streaming = False
    stream_error = ""
    if stream_response is not None and 200 <= stream_response.status_code < 300:
        supports_streaming = _response_has_stream_content(stream_response)
        if not supports_streaming:
            stream_error = "stream probe returned without visible chunks"
    else:
        supports_streaming = None if stream_exception is not None else False
        stream_error = str(stream_exception) if stream_exception else _response_error_text(stream_response)

    status = "ok"
    if supports_tools is False and _looks_like_tools_unsupported(tool_error):
        status = "tools_unsupported"
    elif supports_tools is None and tools_api_accepted is True:
        status = "partial"
    elif any(
        [
            tool_accept_exception,
            auto_exception,
            forced_exception,
            stream_exception,
            tool_roundtrip_error,
        ]
    ):
        status = "partial"

    latency_ms = _elapsed_ms(probe_started)
    result = {
        "status": status,
        "supports_basic_chat": supports_basic_chat,
        "supports_json_output": supports_json_output,
        "supports_tools": supports_tools,
        "tools_api_accepted": tools_api_accepted,
        "tools_auto_call": tools_auto_call,
        "tools_forced_choice": tools_forced_choice,
        "tools_result_roundtrip": tools_result_roundtrip,
        "supports_streaming": supports_streaming,
        "chat_error": basic_error,
        "tool_error": tool_error,
        "tool_api_error": tool_api_error,
        "auto_tool_error": auto_tool_error,
        "forced_tool_error": forced_tool_error,
        "tool_roundtrip_error": tool_roundtrip_error,
        "stream_error": stream_error,
        "probe_profile": profile["provider_id"],
        "probe_tool_choice_modes": profile["tool_choice_modes"],
        "latency_ms": latency_ms,
        "chat_latency_ms": chat_latency_ms,
        "tool_accept_latency_ms": tool_accept_latency_ms,
        "auto_tool_latency_ms": auto_tool_latency_ms,
        "forced_tool_latency_ms": forced_tool_latency_ms,
        "tool_roundtrip_latency_ms": tool_roundtrip_latency_ms,
        "stream_latency_ms": stream_latency_ms,
        **context_info,
    }
    result["recommended_roles"] = recommend_model_roles(result)
    return result


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
    model_id = str(model_info.get("id") or "")
    api_key = str(model_info.get("api_key") or model_info.get("api_key_value") or "")
    if not api_key:
        api_key = _lucode_api_key_for_model_id(model_id)

    return {
        "id": model_id,
        "model_name": model_info.get("model_name") or "",
        "backend_type": model_info.get("backend_type") or "",
        "base_url": model_info.get("base_url") or "",
        "api_key": api_key,
        "provider": model_info.get("provider") or model_info.get("provider_id") or "",
        "provider_ref": model_info.get("provider_ref") or model_info.get("shared_config_group") or "",
        "context_window_tokens": _first_int_value(
            model_info,
            "context_window_tokens",
            "context_length",
            "max_context_tokens",
            "max_input_tokens",
        ),
    }


def probe_profile_for_model(model_info: dict) -> dict[str, Any]:
    """Return a provider-aware probe strategy.

    Provider APIs are OpenAI-compatible in shape, but tool_choice support is not
    identical. The profile keeps probing conservative: auto tool calls are the
    main signal for broad compatibility, while forced tool_choice is only used
    where the provider contract is stable enough.
    """

    base_url = str(model_info.get("base_url") or "").lower()
    model_name = str(model_info.get("model_name") or "").lower()
    provider = " ".join(
        [
            str(model_info.get("provider") or ""),
            str(model_info.get("provider_ref") or ""),
            str(model_info.get("backend_type") or ""),
            base_url,
            model_name,
        ]
    ).lower()

    profile = {
        "provider_id": "openai_compatible",
        "tool_choice_modes": ["auto", "forced"],
        "chat_timeout": 6.0,
        "tool_timeout": 2.0,
        "stream_timeout": 2.0,
        "roundtrip_enabled": True,
        "context_window_tokens": None,
        "context_source": "unknown",
    }

    if "api.openai.com" in provider or "openai/" in model_name or model_name.startswith("gpt-"):
        profile["provider_id"] = "openai"
        profile["context_window_tokens"] = _openai_context_window(model_name)
        profile["context_source"] = "provider_profile" if profile["context_window_tokens"] else "unknown"
        return profile
    if "openrouter" in provider:
        profile["provider_id"] = "openrouter"
        profile["chat_timeout"] = 8.0
        profile["context_source"] = "provider_dynamic"
        return profile
    if "deepseek" in provider:
        profile["provider_id"] = "deepseek"
        profile["tool_choice_modes"] = ["auto"]
        profile["chat_timeout"] = 8.0
        profile["tool_timeout"] = 3.0
        profile["context_window_tokens"] = 65536
        profile["context_source"] = "provider_profile"
        return profile
    if "dashscope" in provider or "aliyuncs.com" in provider or "qwen" in model_name:
        profile["provider_id"] = "dashscope"
        profile["tool_choice_modes"] = ["auto"]
        profile["chat_timeout"] = 8.0
        profile["context_window_tokens"] = _qwen_context_window(model_name)
        profile["context_source"] = "provider_profile" if profile["context_window_tokens"] else "unknown"
        return profile
    if "siliconflow" in provider:
        profile["provider_id"] = "siliconflow"
        profile["tool_choice_modes"] = ["auto"]
        profile["chat_timeout"] = 8.0
        return profile
    if "xiaomimimo" in provider or "mimo" in provider:
        profile["provider_id"] = "mimo"
        profile["tool_choice_modes"] = ["auto"]
        profile["chat_timeout"] = 8.0
        profile["tool_timeout"] = 3.0
        return profile
    if str(model_info.get("backend_type") or "") in {"ollama", "llama_cpp", "local"}:
        profile["provider_id"] = "local_openai_compatible"
        profile["tool_choice_modes"] = ["auto"]
        profile["chat_timeout"] = 4.0
        profile["tool_timeout"] = 2.0
        return profile
    return profile


def estimate_context_window(model_info: dict, profile: dict) -> dict[str, Any]:
    configured = _first_int_value(
        model_info,
        "context_window_tokens",
        "context_length",
        "max_context_tokens",
        "max_input_tokens",
    )
    if configured:
        tokens = configured
        source = "user_config"
    else:
        tokens = profile.get("context_window_tokens")
        source = str(profile.get("context_source") or "unknown")
    return {
        "context_window_tokens": tokens,
        "context_tier": _context_tier(tokens),
        "context_source": source if tokens else "unknown",
    }


def recommend_model_roles(probe_result: dict) -> list[str]:
    roles: list[str] = []
    if probe_result.get("supports_basic_chat") is True:
        roles.append("query_refiner")
        roles.append("final_synthesizer")
    context_tokens = int(probe_result.get("context_window_tokens") or 0)
    if probe_result.get("supports_json_output") is True and context_tokens >= 32768:
        roles.append("orchestrator")
    if probe_result.get("supports_tools") is True:
        roles.append("executor")
    return roles


def _first_int_value(source: dict, *keys: str) -> int | None:
    for key in keys:
        raw = source.get(key)
        if raw is None or raw == "":
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _context_tier(tokens: int | None) -> str:
    if not tokens:
        return "unknown"
    if tokens >= 200000:
        return "very_long"
    if tokens >= 65536:
        return "long"
    if tokens >= 32768:
        return "medium"
    return "standard"


def _openai_context_window(model_name: str) -> int | None:
    name = str(model_name or "").lower()
    if "gpt-5.2" in name:
        return 400000
    if "gpt-4.1" in name:
        return 1000000
    if name.startswith("gpt-5") or name.startswith("gpt-4o"):
        return 128000
    return None


def _qwen_context_window(model_name: str) -> int | None:
    name = str(model_name or "").lower()
    if "long" in name or "1m" in name or "million" in name:
        return 1000000
    if "qwen3" in name or "qwen-max" in name or "qwen-plus" in name:
        return 131072
    return None


def _probe_step_timeout(env_name: str, fallback: float, profile: dict, profile_key: str) -> float:
    return _env_float(env_name, float(profile.get(profile_key) or fallback))


def _tool_probe_payload(model_name: str, tools: list[dict], mode: str, profile: dict) -> dict[str, Any]:
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "Please call the capability_probe function with value ping. Do not answer directly.",
            }
        ],
        "tools": tools,
        "stream": False,
    }
    if mode == "auto":
        payload["tool_choice"] = "auto"
    elif mode == "forced":
        payload["tool_choice"] = {"type": "function", "function": {"name": "capability_probe"}}
        payload["messages"] = [{"role": "user", "content": "Call the capability_probe tool with value ping."}]
    elif mode == "required":
        payload["tool_choice"] = "required"
    if profile.get("provider_id") == "dashscope":
        payload["enable_thinking"] = False
    return payload


def _lucode_api_key_for_model_id(model_id: str) -> str:
    try:
        from runtime.config.model_config import configured_provider_model_definitions

        for item in configured_provider_model_definitions():
            if item.get("id") == model_id:
                return str(item.get("api_key_value") or "")
    except Exception:
        return ""
    return ""


def _post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(endpoint, headers=headers, json=payload, timeout=timeout)
    finally:
        session.close()


def _safe_post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    try:
        return _post_json(endpoint, headers, payload, timeout), None
    except Exception as exc:
        return None, exc


def _timed_post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    started = time.perf_counter()
    response, error = _safe_post_json(endpoint, headers, payload, timeout)
    return response, error, _elapsed_ms(started)


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


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
    return _first_tool_call(response) is not None


def _first_tool_call(response) -> dict | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return None
    first = tool_calls[0]
    return first if isinstance(first, dict) else None


def _capability_probe_tool_schema() -> dict:
    return {
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


def _tool_roundtrip_payload(model_name: str, tool_call: dict) -> dict:
    call_id = str(tool_call.get("id") or "call_capability_probe")
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(function.get("name") or "capability_probe")
    arguments = str(function.get("arguments") or '{"value":"ping"}')
    return {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "Please call the capability_probe function with value ping. Do not answer directly.",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": '{"ok": true, "value": "ping"}',
            },
        ],
        "tools": [_capability_probe_tool_schema()],
        "stream": False,
    }


def _response_has_stream_content(response) -> bool:
    iter_lines = getattr(response, "iter_lines", None)
    if callable(iter_lines):
        try:
            for raw_line in iter_lines(decode_unicode=True):
                line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line or "")
                if line.strip() and line.strip() != "data: [DONE]":
                    return True
        except TypeError:
            pass
    text = str(getattr(response, "text", "") or "")
    return bool(text.strip())


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
        "config_incomplete",
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
