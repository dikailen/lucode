from __future__ import annotations


class TurnStateGuard:
    def __init__(self) -> None:
        self._current_id = 0
        self._running = False
        self._stopping = False

    def start(self) -> int:
        self._current_id += 1
        self._running = True
        self._stopping = False
        return self._current_id

    def request_stop(self, turn_id: int) -> bool:
        if not self.is_current(turn_id):
            return False
        self._stopping = True
        return True

    def finish_if_current(self, turn_id: int) -> bool:
        if not self.is_current(turn_id):
            return False
        self._running = False
        self._stopping = False
        return True

    def is_current(self, turn_id: int) -> bool:
        return self._running and self._current_id == int(turn_id)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    @property
    def can_start_new_turn(self) -> bool:
        return not self._running and not self._stopping
