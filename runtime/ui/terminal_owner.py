from __future__ import annotations

from contextvars import ContextVar


_DYNAMIC_OWNER: ContextVar[str] = ContextVar("lucode_dynamic_terminal_owner", default="")


def current_dynamic_owner() -> str:
    return _DYNAMIC_OWNER.get()


def set_dynamic_owner(owner: str):
    return _DYNAMIC_OWNER.set(str(owner or "").strip())


def reset_dynamic_owner(token) -> None:
    _DYNAMIC_OWNER.reset(token)


def rich_live_owns_terminal() -> bool:
    return current_dynamic_owner() == "rich_live"
