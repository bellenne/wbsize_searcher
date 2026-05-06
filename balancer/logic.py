from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_RETRYABLE_ERRORS = {
    "SIZE_NOT_FOUND",
    "PAGE_NOT_READY",
    "NAVIGATION_TIMEOUT",
    "TEMPORARY_SITE_ERROR",
    "WORKER_BUSY",
    "WORKER_TIMEOUT",
}
TASK_NON_RETRYABLE_ERRORS = {"AUTH_REQUIRED", "INVALID_INPUT", "WORKER_NOT_AUTHORIZED"}
CALLBACK_BACKOFF_SECONDS = [0, 10, 30, 60, 180]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_workers(raw: str) -> dict[str, str]:
    workers: dict[str, str] = {}
    for index, item in enumerate(part.strip() for part in raw.split(",") if part.strip()):
        if "=" in item:
            worker_id, base_url = item.split("=", 1)
            workers[worker_id.strip()] = base_url.strip().rstrip("/")
        else:
            workers[f"worker-{index + 1}"] = item.rstrip("/")
    return workers


def is_retryable_error(error_code: str | None, retryable: bool | None = None) -> bool:
    if error_code in TASK_NON_RETRYABLE_ERRORS:
        return False
    if error_code == "BROWSER_ERROR":
        return bool(retryable)
    if error_code in TASK_RETRYABLE_ERRORS:
        return True
    return bool(retryable)


def callback_delay(attempt_index: int) -> int:
    if attempt_index < len(CALLBACK_BACKOFF_SECONDS):
        return CALLBACK_BACKOFF_SECONDS[attempt_index]
    return CALLBACK_BACKOFF_SECONDS[-1]


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def should_force_refresh(last_refresh_at: str | None, ttl_seconds: int, retry_attempt: bool) -> bool:
    if retry_attempt:
        return True
    timestamp = parse_timestamp(last_refresh_at)
    if timestamp is None:
        return True
    return time.time() - timestamp > ttl_seconds


@dataclass(slots=True)
class WorkerState:
    worker_id: str
    base_url: str
    online: bool = False
    status: str = "offline"
    authorized: bool = False
    browser_ready: bool = False
    current_task_id: str | None = None
    last_refresh_at: str | None = None
    last_task_at: str | None = None
    last_error: str | None = None
    last_seen_at: str | None = None

    def eligible(self) -> bool:
        return (
            self.online
            and self.status == "idle"
            and self.authorized
            and self.browser_ready
            and not self.current_task_id
        )


@dataclass(slots=True)
class SearchSizeTaskIn:
    order_number: str
    external_task_id: str | None = None
    callback_url: str | None = None
    callback_token: str | None = None
    cabinet: str | None = None
    account: str | None = None
    metadata: dict[str, object] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    external_task_id TEXT,
                    order_number TEXT NOT NULL,
                    callback_url TEXT,
                    callback_token TEXT,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    callback_attempts INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    callback_delivered_at TEXT
                )
                """
            )

    def insert_task(self, task_id: str, payload: SearchSizeTaskIn, status: str) -> None:
        now = utc_now()
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, external_task_id, order_number, callback_url, callback_token,
                    status, payload_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    payload.external_task_id,
                    payload.order_number,
                    payload.callback_url,
                    payload.callback_token,
                    status,
                    payload.to_json(),
                    json.dumps(payload.metadata or {}),
                    now,
                    now,
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [task_id]
        with self.lock, self.connect() as connection:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)
