from __future__ import annotations

from threading import Lock


class WorkerTaskLock:
    def __init__(self) -> None:
        self._lock = Lock()
        self.current_task_id: str | None = None

    def try_begin(self, task_id: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.current_task_id = task_id
        return True

    def finish(self) -> None:
        self.current_task_id = None
        try:
            self._lock.release()
        except RuntimeError:
            pass
