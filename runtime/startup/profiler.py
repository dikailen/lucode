from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


def startup_profiling_enabled() -> bool:
    raw = str(os.environ.get("LUCODE_STARTUP_PROFILE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "debug", "profile"}


@dataclass
class StartupProfiler:
    enabled: bool = field(default_factory=startup_profiling_enabled)
    _start: float = field(default_factory=time.perf_counter)
    _last: float = field(default_factory=time.perf_counter)
    _events: list[tuple[str, float, float]] = field(default_factory=list)

    def mark(self, label: str) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        self._events.append((str(label), now - self._start, now - self._last))
        self._last = now

    def render(self) -> str:
        if not self.enabled:
            return ""
        lines = ["Lucode startup profile"]
        for label, total, delta in self._events:
            lines.append(f"- {label}: +{delta * 1000:.1f}ms / {total * 1000:.1f}ms")
        return "\n".join(lines)

    def print(self) -> None:
        output = self.render()
        if output:
            print(output)
