from __future__ import annotations


STREAMED_OUTPUT_SUPPRESSION_MIN_CHARS = 8


def streamed_output_is_sufficient(hooks) -> bool:
    if not getattr(hooks, "streamed_output_seen", False):
        return False
    return int(getattr(hooks, "streamed_output_chars", 0) or 0) >= STREAMED_OUTPUT_SUPPRESSION_MIN_CHARS
