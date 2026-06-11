from __future__ import annotations

import os

from runtime.agents.sdk import runner_class


async def run_agent_once(agent, run_input, hooks, max_turns=20, stream_output: bool | None = None, on_delta=None):
    """Run one SDK segment; stream visible answer deltas when the provider supports it."""

    Runner = runner_class()
    if stream_output is False or not streaming_enabled():
        return await Runner.run(agent, run_input, hooks=hooks, max_turns=max_turns)

    result = Runner.run_streamed(agent, run_input, hooks=hooks, max_turns=max_turns)
    printed_any = False
    try:
        async for event in result.stream_events():
            delta = stream_delta_text(event)
            if not delta:
                continue
            if not printed_any:
                print("\n", end="", flush=True)
                printed_any = True
                setattr(hooks, "streamed_output_seen", True)
            setattr(hooks, "streamed_output_chars", int(getattr(hooks, "streamed_output_chars", 0) or 0) + len(delta))
            if on_delta is not None:
                try:
                    on_delta(delta)
                except Exception:
                    pass
            print(delta, end="", flush=True)
    except Exception as exc:
        if not (printed_any and _is_recoverable_stream_tail_error(exc)):
            raise
    if printed_any:
        print()
    return result


def streaming_enabled() -> bool:
    raw = str(os.environ.get("AGENTS_STREAM_OUTPUT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def stream_delta_text(event) -> str:
    if getattr(event, "type", "") != "raw_response_event":
        return ""
    data = getattr(event, "data", None)
    event_type = str(getattr(data, "type", ""))
    if event_type not in {"response.output_text.delta", "response.text.delta"}:
        return ""
    return str(getattr(data, "delta", "") or "")


def _is_recoverable_stream_tail_error(exc: Exception) -> bool:
    name = exc.__class__.__name__
    text = str(exc).lower()
    if name in {"RemoteProtocolError", "StreamConsumed", "ReadError"}:
        return True
    return any(
        marker in text
        for marker in [
            "incomplete chunked read",
            "peer closed connection without sending complete message body",
            "response ended prematurely",
        ]
    )
