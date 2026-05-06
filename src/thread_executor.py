from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class _Command:
    callback: Callable[[], Any]
    done: threading.Event
    result: Any = None
    error: BaseException | None = None


class ThreadExecutor:
    """Runs all Playwright Sync API calls on one long-lived thread."""

    def __init__(self, name: str) -> None:
        self._queue: queue.Queue[_Command] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def run(self, callback: Callable[[], Any], timeout: float | None = None) -> Any:
        command = _Command(callback=callback, done=threading.Event())
        self._queue.put(command)
        if not command.done.wait(timeout):
            raise TimeoutError("Timed out waiting for browser thread command.")
        if command.error is not None:
            raise command.error
        return command.result

    def submit(self, callback: Callable[[], Any]) -> None:
        command = _Command(callback=callback, done=threading.Event())
        self._queue.put(command)

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            try:
                command.result = command.callback()
            except BaseException as exc:
                command.error = exc
            finally:
                command.done.set()
                self._queue.task_done()
