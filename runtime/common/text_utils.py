from __future__ import annotations


def sanitize_text(value: str) -> str:
    """Remove invalid Unicode surrogate characters before sending text to model APIs."""

    return str(value).encode("utf-8", errors="ignore").decode("utf-8")
