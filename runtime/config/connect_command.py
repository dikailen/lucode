from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.config.model_config import connect_provider, load_provider_catalog, normalize_provider_id


@dataclass(frozen=True)
class ProviderConnectRequest:
    provider: str
    api_key: str = ""
    homepage: str = ""
    base_url: str = ""
    display_name: str = ""
    models: tuple[str, ...] = ()
    custom: bool = False

    @property
    def normalized_provider(self) -> str:
        return normalize_provider_id(self.provider)


def parse_slash_connect_command(command: str) -> ProviderConnectRequest | None:
    tokens = _split_command(command)
    if not tokens or tokens[0].lower() != "/connect" or len(tokens) < 2:
        return None
    if not any(token.startswith("--") for token in tokens[2:]):
        return None
    return _request_from_tokens(tokens[1], tokens[2:])


def request_from_cli_args(args) -> ProviderConnectRequest:
    models = list(getattr(args, "model", None) or [])
    models.extend(_split_models_argument(getattr(args, "models", "") or ""))
    return ProviderConnectRequest(
        provider=str(getattr(args, "provider", "") or ""),
        api_key=str(getattr(args, "api_key", "") or ""),
        homepage=str(getattr(args, "homepage", "") or ""),
        base_url=str(getattr(args, "base_url", "") or ""),
        display_name=str(getattr(args, "display_name", "") or ""),
        models=tuple(item for item in models if str(item).strip()),
        custom=bool(getattr(args, "custom", False)),
    )


def provider_requires_api_key(request: ProviderConnectRequest) -> bool:
    if request.custom:
        return True
    provider_id = request.normalized_provider
    preset = load_provider_catalog().get(provider_id) or {}
    return not bool(preset.get("local"))


def apply_provider_connect_request(
    request: ProviderConnectRequest,
    *,
    workspace_root: Path | str,
    user_home: Path | str,
) -> dict[str, Any]:
    if provider_requires_api_key(request) and not request.api_key:
        raise ValueError("还缺 API key，未写入项目配置。")
    return connect_provider(
        request.provider,
        api_key=request.api_key or None,
        workspace_root=workspace_root,
        user_home=user_home,
        homepage=request.homepage or None,
        base_url=request.base_url or None,
        models=list(request.models) or None,
        display_name=request.display_name or None,
        custom=request.custom,
    )


def render_provider_connect_success(result: dict[str, Any], *, api_key_provided: bool) -> str:
    provider = result["provider"]
    lines = [
        f"已连接 Provider：{provider.get('display_name')}（{result['provider_id']}）",
        f"官网：{provider.get('homepage')}",
        f"请求地址：{provider.get('base_url')}",
    ]
    if provider.get("local"):
        lines.append("本地 Provider 无需 API key。")
    elif api_key_provided:
        lines.append("API key 已保存到用户级 auth.json，未写入项目配置。")
    else:
        lines.append("API key 未保存；稍后可重新运行 connect 命令补上。")
    return "\n".join(lines)


def redact_connect_secret(text: str, request: ProviderConnectRequest | None) -> str:
    output = str(text or "")
    if request is not None and request.api_key:
        output = output.replace(request.api_key, "<hidden>")
    output = re.sub(r"sk-[A-Za-z0-9._-]+", "sk-***", output)
    return output


def _request_from_tokens(provider: str, tokens: list[str]) -> ProviderConnectRequest:
    values: dict[str, Any] = {
        "provider": provider,
        "api_key": "",
        "homepage": "",
        "base_url": "",
        "display_name": "",
        "models": [],
        "custom": False,
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name, inline_value = _split_option(token)
        if name == "--custom":
            values["custom"] = True
            index += 1
            continue
        if name not in {"--api-key", "--homepage", "--base-url", "--display-name", "--model", "--models"}:
            raise ValueError(f"未知 /connect 参数：{token}")
        value, index = _option_value(name, inline_value, tokens, index)
        if name == "--api-key":
            values["api_key"] = value
        elif name == "--homepage":
            values["homepage"] = value
        elif name == "--base-url":
            values["base_url"] = value
        elif name == "--display-name":
            values["display_name"] = value
        elif name == "--model":
            values["models"].append(value)
        elif name == "--models":
            values["models"].extend(_split_models_argument(value))
    return ProviderConnectRequest(
        provider=values["provider"],
        api_key=values["api_key"],
        homepage=values["homepage"],
        base_url=values["base_url"],
        display_name=values["display_name"],
        models=tuple(values["models"]),
        custom=values["custom"],
    )


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(str(command or ""), comments=False, posix=True)
    except ValueError as exc:
        raise ValueError(f"/connect 参数解析失败：{exc}") from exc


def _split_option(token: str) -> tuple[str, str | None]:
    if not token.startswith("--"):
        raise ValueError(f"无法识别 /connect 参数：{token}")
    if "=" not in token:
        return token, None
    name, value = token.split("=", 1)
    return name, value


def _option_value(name: str, inline_value: str | None, tokens: list[str], index: int) -> tuple[str, int]:
    if inline_value is not None:
        if not inline_value:
            raise ValueError(f"{name} 需要一个值。")
        return inline_value, index + 1
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
        raise ValueError(f"{name} 需要一个值。")
    return tokens[index + 1], index + 2


def _split_models_argument(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", str(value or "")) if item.strip()]
