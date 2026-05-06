from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("BALANCER_DATA_DIR", str(ROOT / "test-data"))
os.environ.setdefault("WORKERS", "worker-1=http://worker-1:8000")
os.environ.setdefault("TARGET_URL", "https://example.org")

from balancer.logic import (  # noqa: E402
    SearchSizeTaskIn,
    TaskStore,
    WorkerState,
    callback_delay,
    is_retryable_error,
    parse_workers,
    should_force_refresh,
)
from worker_state import WorkerTaskLock  # noqa: E402


class BalancerLogicTests(unittest.TestCase):
    def test_parse_workers_supports_named_entries(self) -> None:
        workers = parse_workers("w1=http://worker-1:8000,w2=http://worker-2:8000")
        self.assertEqual(workers["w1"], "http://worker-1:8000")
        self.assertEqual(workers["w2"], "http://worker-2:8000")

    def test_worker_eligible_requires_idle_authorized_ready(self) -> None:
        worker = WorkerState(
            worker_id="w1",
            base_url="http://worker-1:8000",
            online=True,
            status="idle",
            authorized=True,
            browser_ready=True,
        )
        self.assertTrue(worker.eligible())
        worker.current_task_id = "task"
        self.assertFalse(worker.eligible())

    def test_retry_decision(self) -> None:
        self.assertTrue(is_retryable_error("SIZE_NOT_FOUND", True))
        self.assertTrue(is_retryable_error("WORKER_TIMEOUT", True))
        self.assertFalse(is_retryable_error("AUTH_REQUIRED", True))
        self.assertFalse(is_retryable_error("INVALID_INPUT", True))

    def test_force_refresh_ttl_and_retry(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        self.assertTrue(should_force_refresh(None, ttl_seconds=300, retry_attempt=False))
        self.assertTrue(should_force_refresh(old, ttl_seconds=300, retry_attempt=False))
        self.assertFalse(should_force_refresh(fresh, ttl_seconds=300, retry_attempt=False))
        self.assertTrue(should_force_refresh(fresh, ttl_seconds=300, retry_attempt=True))

    def test_callback_backoff(self) -> None:
        self.assertEqual(callback_delay(0), 0)
        self.assertEqual(callback_delay(2), 30)
        self.assertEqual(callback_delay(99), 180)

    def test_task_status_transitions_are_persisted(self) -> None:
        data_dir = ROOT / "test-data"
        data_dir.mkdir(exist_ok=True)
        db_path = data_dir / f"{uuid.uuid4()}.sqlite"
        store = TaskStore(db_path)
        task = SearchSizeTaskIn(order_number="123", callback_url="http://laravel/callback")
        store.insert_task("task-1", task, "queued")
        store.update_task("task-1", status="running", attempts=1)
        store.update_task("task-1", status="completed", result_json='{"size":"42"}')
        saved = store.get_task("task-1")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["attempts"], 1)


class WorkerBusyLockTests(unittest.TestCase):
    def test_worker_busy_lock_rejects_second_task(self) -> None:
        lock = WorkerTaskLock()
        self.assertTrue(lock.try_begin("task-1"))
        self.assertFalse(lock.try_begin("task-2"))
        lock.finish()
        self.assertTrue(lock.try_begin("task-3"))
        lock.finish()


if __name__ == "__main__":
    unittest.main()
