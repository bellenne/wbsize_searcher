from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


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


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(slots=True)
class BalancerConfig:
    workers: dict[str, str]
    task_max_attempts: int
    task_timeout_seconds: int
    callback_max_attempts: int
    callback_timeout_seconds: int
    worker_refresh_ttl_seconds: int
    worker_status_poll_interval_seconds: int
    data_dir: Path
    default_callback_token: str | None
    requeue_unfinished_on_startup: bool
    log_level: str
    api_host: str
    api_port: int
    auth_phone_selector: str
    auth_code_selector: str
    cabinet_profile_selector: str
    cabinet_hover_wait_ms: int
    cabinet_post_click_wait_ms: int
    close_modal_selector: str
    close_modal_action_name: str
    close_modal_extra_wait_ms: int

    @classmethod
    def from_env(cls) -> "BalancerConfig":
        load_dotenv(override=False)
        data_dir = Path(os.getenv("BALANCER_DATA_DIR", "/app/balancer-data")).expanduser()
        return cls(
            workers=parse_workers(os.getenv("WORKERS", "")),
            task_max_attempts=parse_int("TASK_MAX_ATTEMPTS", 2),
            task_timeout_seconds=parse_int("TASK_TIMEOUT_SECONDS", 180),
            callback_max_attempts=parse_int("CALLBACK_MAX_ATTEMPTS", 5),
            callback_timeout_seconds=parse_int("CALLBACK_TIMEOUT_SECONDS", 15),
            worker_refresh_ttl_seconds=parse_int("WORKER_REFRESH_TTL_SECONDS", 300),
            worker_status_poll_interval_seconds=parse_int("WORKER_STATUS_POLL_INTERVAL_SECONDS", 5),
            data_dir=data_dir,
            default_callback_token=os.getenv("DEFAULT_CALLBACK_TOKEN", "").strip() or None,
            requeue_unfinished_on_startup=parse_bool(os.getenv("REQUEUE_UNFINISHED_ON_STARTUP"), False),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            api_host=os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0",
            api_port=parse_int("API_PORT", 8000),
            auth_phone_selector=os.getenv("AUTH_PHONE_SELECTOR", 'input[placeholder="999 999-99-99"]').strip()
            or 'input[placeholder="999 999-99-99"]',
            auth_code_selector=os.getenv("AUTH_CODE_SELECTOR", 'input[data-testid="sms-code-input"]').strip()
            or 'input[data-testid="sms-code-input"]',
            cabinet_profile_selector=os.getenv("CABINET_PROFILE_SELECTOR", ".ProfileView").strip() or ".ProfileView",
            cabinet_hover_wait_ms=parse_int("CABINET_HOVER_WAIT_MS", 500),
            cabinet_post_click_wait_ms=parse_int("CABINET_POST_CLICK_WAIT_MS", 3000),
            close_modal_selector=os.getenv(
                "CLOSE_MODAL_SELECTOR",
                "#Portal-drawer [data-name='Overlay'] button[type='button']",
            ).strip()
            or "#Portal-drawer [data-name='Overlay'] button[type='button']",
            close_modal_action_name=os.getenv("CLOSE_MODAL_ACTION_NAME", "close pvz modal first button").strip()
            or "close pvz modal first button",
            close_modal_extra_wait_ms=parse_int("CLOSE_MODAL_EXTRA_WAIT_MS", 3000),
        )


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


def model_to_json(model: BaseModel) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json()


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

    def as_dict(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "base_url": self.base_url,
            "online": self.online,
            "status": self.status,
            "authorized": self.authorized,
            "browser_ready": self.browser_ready,
            "current_task_id": self.current_task_id,
            "last_refresh_at": self.last_refresh_at,
            "last_task_at": self.last_task_at,
            "last_error": self.last_error,
            "last_seen_at": self.last_seen_at,
        }


class SearchSizeTaskIn(BaseModel):
    external_task_id: str | None = None
    order_number: str
    callback_url: str | None = None
    callback_token: str | None = None
    cabinet: str | None = None
    account: str | None = None
    metadata: dict[str, object] | None = None


class JsonHttpClient:
    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 15,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = UrlRequest(url=url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return exc.code, {"detail": raw}
        except URLError as exc:
            raise TimeoutError(str(exc)) from exc

    def request_status_only(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 15,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = UrlRequest(url=url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return response.status, raw
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, raw
        except URLError as exc:
            raise TimeoutError(str(exc)) from exc


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
                    model_to_json(payload),
                    json.dumps(payload.metadata or {}),
                    now,
                    now,
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [task_id]
        with self.lock, self.connect() as connection:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)

    def load_unfinished_for_queue(self) -> list[str]:
        with self.lock, self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET status = 'queued', updated_at = ? WHERE status IN ('running', 'retrying')",
                (utc_now(),),
            )
            rows = connection.execute(
                "SELECT task_id FROM tasks WHERE status IN ('queued', 'retrying') ORDER BY created_at ASC"
            ).fetchall()
        return [str(row["task_id"]) for row in rows]


class BalancerService:
    def __init__(self, config: BalancerConfig, store: TaskStore, http: JsonHttpClient) -> None:
        self.config = config
        self.store = store
        self.http = http
        self.queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.workers = {
            worker_id: WorkerState(worker_id=worker_id, base_url=base_url)
            for worker_id, base_url in config.workers.items()
        }
        self.worker_lock = threading.Lock()
        self.callback_lock = threading.Lock()
        self.callbacks_in_progress: set[str] = set()
        self.logger = logging.getLogger("wbsize_balancer")

    def start(self) -> None:
        if self.config.requeue_unfinished_on_startup:
            for task_id in self.store.load_unfinished_for_queue():
                self.queue.put(task_id)
        threading.Thread(target=self.poll_workers_loop, name="worker-status-poller", daemon=True).start()
        dispatcher_count = max(1, len(self.workers))
        for index in range(dispatcher_count):
            threading.Thread(
                target=self.dispatch_loop,
                name=f"task-dispatcher-{index + 1}",
                daemon=True,
            ).start()

    def stop(self) -> None:
        self.stop_event.set()

    def accept_task(self, payload: SearchSizeTaskIn) -> str:
        task_id = str(uuid.uuid4())
        self.store.insert_task(task_id, payload, "queued")
        self.queue.put(task_id)
        self.logger.info("task accepted task_id=%s external_task_id=%s order_number=%s", task_id, payload.external_task_id, payload.order_number)
        return task_id

    def poll_workers_loop(self) -> None:
        while not self.stop_event.is_set():
            for worker_id in list(self.workers):
                self.refresh_worker_status(worker_id)
            self.stop_event.wait(self.config.worker_status_poll_interval_seconds)

    def refresh_worker_status(self, worker_id: str) -> None:
        worker = self.workers[worker_id]
        try:
            _status, payload = self.http.request("GET", urljoin(worker.base_url + "/", "status"), timeout=5)
            with self.worker_lock:
                worker.online = True
                worker.status = str(payload.get("status") or "unknown")
                worker.authorized = bool(payload.get("authorized"))
                worker.browser_ready = bool(payload.get("browser_ready"))
                worker.current_task_id = payload.get("current_task_id")
                worker.last_refresh_at = payload.get("last_refresh_at") or worker.last_refresh_at
                worker.last_task_at = payload.get("last_task_at")
                worker.last_error = payload.get("last_error")
                worker.last_seen_at = utc_now()
        except Exception as exc:
            with self.worker_lock:
                worker.online = False
                worker.status = "offline"
                worker.last_error = str(exc)
            self.logger.warning("worker offline worker_id=%s error=%s", worker_id, exc)

    def select_worker(self) -> WorkerState | None:
        with self.worker_lock:
            eligible = [worker for worker in self.workers.values() if worker.eligible()]
            eligible.sort(key=lambda worker: worker.last_task_at or "")
            return eligible[0] if eligible else None

    def select_and_reserve_worker(self, task_id: str) -> WorkerState | None:
        with self.worker_lock:
            eligible = [worker for worker in self.workers.values() if worker.eligible()]
            eligible.sort(key=lambda worker: worker.last_task_at or "")
            if not eligible:
                return None
            worker = eligible[0]
            worker.status = "busy"
            worker.current_task_id = task_id
            worker.last_task_at = utc_now()
            return worker

    def dispatch_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                task_id = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.process_task(task_id)
            finally:
                self.queue.task_done()

    def process_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if not task or task["status"] not in {"queued", "retrying"}:
            return
        worker = self.select_and_reserve_worker(task_id)
        if worker is None:
            self.queue.put(task_id)
            self.stop_event.wait(self.config.worker_status_poll_interval_seconds)
            return

        attempts = int(task["attempts"]) + 1
        force_refresh = should_force_refresh(
            worker.last_refresh_at,
            self.config.worker_refresh_ttl_seconds,
            retry_attempt=attempts > 1,
        )
        self.store.update_task(task_id, status="running", worker_id=worker.worker_id, attempts=attempts)
        self.logger.info(
            "attempt started task_id=%s worker_id=%s attempt=%s force_refresh=%s",
            task_id,
            worker.worker_id,
            attempts,
            force_refresh,
        )

        payload = json.loads(task["payload_json"])
        worker_payload = {
            "task_id": task_id,
            "external_task_id": task["external_task_id"],
            "order_number": task["order_number"],
            "force_refresh": force_refresh,
            "close_modal": True,
            "cabinet": payload.get("cabinet"),
            "account": payload.get("account"),
            "attempt": attempts,
            "metadata": json.loads(task["metadata_json"] or "{}"),
        }

        try:
            status_code, response = self.http.request(
                "POST",
                urljoin(worker.base_url + "/", "task/search-size"),
                payload=worker_payload,
                timeout=self.config.task_timeout_seconds,
            )
            if status_code == 409:
                response = response.get("detail", response)
            self.handle_worker_response(task, worker, attempts, response)
        except Exception as exc:
            self.mark_worker_error(worker.worker_id, str(exc))
            error = {"error_code": "WORKER_TIMEOUT", "message": str(exc), "retryable": True}
            self.handle_worker_response(task, worker, attempts, {"success": False, **error})

    def handle_worker_response(
        self,
        task: dict[str, Any],
        worker: WorkerState,
        attempts: int,
        response: dict[str, Any],
    ) -> None:
        task_id = task["task_id"]
        if response.get("success") is True:
            result = {
                "order_number": task["order_number"],
                "size": response.get("size"),
            }
            self.store.update_task(
                task_id,
                status="completed",
                result_json=json.dumps(result, ensure_ascii=False),
                error_json=None,
                completed_at=utc_now(),
            )
            self.mark_worker_idle(worker.worker_id, last_refresh_at=worker.last_refresh_at or utc_now())
            self.logger.info("task completed task_id=%s worker_id=%s size=%s", task_id, worker.worker_id, response.get("size"))
            self.schedule_callback(task_id)
            return

        error_code = str(response.get("error_code") or "BROWSER_ERROR")
        retryable = is_retryable_error(error_code, bool(response.get("retryable")))
        error = {
            "code": error_code,
            "message": str(response.get("message") or "Worker task failed."),
            "retryable": retryable,
            "details": response.get("details"),
        }
        if error_code == "AUTH_REQUIRED":
            self.mark_worker_auth_required(worker.worker_id, error["message"])
        elif error_code in {"WORKER_TIMEOUT", "BROWSER_ERROR"}:
            self.mark_worker_error(worker.worker_id, error["message"])

        if retryable and attempts < self.config.task_max_attempts:
            self.store.update_task(task_id, status="retrying", error_json=json.dumps(error, ensure_ascii=False))
            self.queue.put(task_id)
            self.logger.info("retry scheduled task_id=%s error_code=%s attempt=%s", task_id, error_code, attempts)
        else:
            self.store.update_task(
                task_id,
                status="failed",
                error_json=json.dumps(error, ensure_ascii=False),
                completed_at=utc_now(),
            )
            self.logger.info("task failed task_id=%s error_code=%s attempts=%s", task_id, error_code, attempts)
            self.schedule_callback(task_id)

    def mark_worker_idle(self, worker_id: str, last_refresh_at: str | None = None) -> None:
        with self.worker_lock:
            worker = self.workers[worker_id]
            worker.status = "idle"
            worker.current_task_id = None
            worker.last_task_at = utc_now()
            if last_refresh_at:
                worker.last_refresh_at = last_refresh_at

    def mark_worker_auth_required(self, worker_id: str, message: str) -> None:
        with self.worker_lock:
            worker = self.workers[worker_id]
            worker.status = "auth_required"
            worker.authorized = False
            worker.last_error = message

    def mark_worker_error(self, worker_id: str, message: str) -> None:
        with self.worker_lock:
            worker = self.workers[worker_id]
            worker.status = "error"
            worker.online = False
            worker.last_error = message

    def schedule_callback(self, task_id: str) -> None:
        threading.Thread(
            target=self.deliver_callback,
            args=(task_id,),
            name=f"callback-{task_id}",
            daemon=True,
        ).start()

    def deliver_callback(self, task_id: str) -> None:
        with self.callback_lock:
            if task_id in self.callbacks_in_progress:
                self.logger.info("callback already in progress task_id=%s", task_id)
                return
            self.callbacks_in_progress.add(task_id)
        try:
            self._deliver_callback_locked(task_id)
        finally:
            with self.callback_lock:
                self.callbacks_in_progress.discard(task_id)

    def _deliver_callback_locked(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if not task or not task["callback_url"]:
            return
        if task["status"] == "callback_delivered":
            return
        self.store.update_task(task_id, status="callback_pending")
        callback_payload = self.build_callback_payload(task)
        token = task["callback_token"] or self.config.default_callback_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        for index in range(self.config.callback_max_attempts):
            delay = callback_delay(index)
            if delay > 0:
                self.stop_event.wait(delay)
            try:
                status_code, response_body = self.http.request_status_only(
                    "POST",
                    task["callback_url"],
                    payload=callback_payload,
                    timeout=self.config.callback_timeout_seconds,
                    headers=headers,
                )
                self.store.update_task(task_id, callback_attempts=index + 1)
                if 200 <= status_code < 300:
                    self.store.update_task(task_id, status="callback_delivered", callback_delivered_at=utc_now())
                    self.logger.info(
                        "callback delivered task_id=%s attempts=%s http_status=%s",
                        task_id,
                        index + 1,
                        status_code,
                    )
                    return
                self.logger.warning(
                    "callback rejected task_id=%s attempt=%s http_status=%s body_prefix=%s",
                    task_id,
                    index + 1,
                    status_code,
                    response_body[:200],
                )
            except Exception as exc:
                self.store.update_task(task_id, callback_attempts=index + 1)
                self.logger.warning("callback failed task_id=%s attempt=%s error=%s", task_id, index + 1, exc)

        self.store.update_task(task_id, status="callback_failed")
        self.logger.error("callback failed permanently task_id=%s", task_id)

    def build_callback_payload(self, task: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(task["result_json"] or "{}")
        error = json.loads(task["error_json"] or "{}")
        final_status = "completed" if task["result_json"] else "failed"
        payload: dict[str, Any] = {
            "external_task_id": task["external_task_id"],
            "balancer_task_id": task["task_id"],
            "status": final_status,
            "success": final_status == "completed",
            "worker_id": task["worker_id"],
            "attempts": task["attempts"],
            "timestamps": {
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
                "completed_at": task["completed_at"],
            },
            "metadata": json.loads(task["metadata_json"] or "{}"),
        }
        if final_status == "completed":
            payload["result"] = result
        else:
            payload["error"] = error
            payload["order_number"] = task["order_number"]
        return payload

    def proxy_worker(self, worker_id: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        worker = self.workers.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        status_code, response = self.http.request("POST", urljoin(worker.base_url + "/", path.lstrip("/")), body, timeout=30)
        if status_code >= 400:
            raise HTTPException(status_code=status_code, detail=response)
        return response

    def proxy_all_workers(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for worker_id in self.workers:
            try:
                results[worker_id] = {"success": True, "response": self.proxy_worker(worker_id, path, body)}
            except HTTPException as exc:
                results[worker_id] = {"success": False, "error": exc.detail}
        return {"success": all(item["success"] for item in results.values()), "workers": results}


config = BalancerConfig.from_env()
logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
store = TaskStore(config.data_dir / "tasks.sqlite")
service = BalancerService(config=config, store=store, http=JsonHttpClient())
app = FastAPI(title="WBSize Balancer", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    service.start()


@app.on_event("shutdown")
def shutdown() -> None:
    service.stop()


@app.get("/health")
def health() -> dict[str, object]:
    return {"alive": True, "timestamp": utc_now(), "workers": len(service.workers)}


@app.post("/tasks/search-size")
def create_search_size_task(payload: SearchSizeTaskIn) -> dict[str, object]:
    if not payload.order_number.strip():
        raise HTTPException(status_code=400, detail={"error_code": "INVALID_INPUT", "message": "order_number is required."})
    task_id = service.accept_task(payload)
    return {"accepted": True, "task_id": task_id, "status": "queued"}


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.get("/tasks")
def list_tasks(limit: int = 100) -> list[dict[str, object]]:
    return store.list_tasks(limit=limit)


@app.get("/workers")
def list_workers() -> list[dict[str, object]]:
    for worker_id in list(service.workers):
        service.refresh_worker_status(worker_id)
    with service.worker_lock:
        return [worker.as_dict() for worker in service.workers.values()]


@app.get("/workers/{worker_id}")
def get_worker(worker_id: str) -> dict[str, object]:
    worker = service.workers.get(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")
    service.refresh_worker_status(worker_id)
    return worker.as_dict()


async def _request_json(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    return json.loads(raw_body.decode("utf-8")) if raw_body else {}


def _simple_phone_payload(body: dict[str, Any]) -> dict[str, Any]:
    phone = str(body.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail={"error_code": "INVALID_INPUT", "message": "phone is required."})
    return {
        "phone": phone,
        "selector": body.get("selector") or config.auth_phone_selector,
        "submit_selector": body.get("submit_selector"),
        "wait_ms": body.get("wait_ms"),
    }


def _simple_code_payload(body: dict[str, Any]) -> dict[str, Any]:
    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail={"error_code": "INVALID_INPUT", "message": "code is required."})
    return {
        "code": code,
        "selector": body.get("selector") or config.auth_code_selector,
        "submit_selector": body.get("submit_selector"),
        "wait_for_networkidle": body.get("wait_for_networkidle", True),
        "extra_wait_ms": body.get("extra_wait_ms"),
    }


def _cabinet_payload(body: dict[str, Any]) -> dict[str, Any]:
    current = str(body.get("in") or body.get("current") or body.get("current_button_text") or "").strip()
    target = str(body.get("out") or body.get("target") or body.get("target_menu_text") or "").strip()
    if not current or not target:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "INVALID_INPUT", "message": "Both 'in' and 'out' are required."},
        )
    return {
        "profile_selector": body.get("profile_selector") or config.cabinet_profile_selector,
        "current_button_text": current,
        "target_menu_text": target,
        "timeout_ms": body.get("timeout_ms"),
        "hover_wait_ms": body.get("hover_wait_ms", config.cabinet_hover_wait_ms),
        "post_click_wait_ms": body.get("post_click_wait_ms", config.cabinet_post_click_wait_ms),
    }


def _close_modal_payload(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    return {
        "selector": body.get("selector") or config.close_modal_selector,
        "action_name": body.get("action_name") or config.close_modal_action_name,
        "wait_for_networkidle": body.get("wait_for_networkidle", False),
        "extra_wait_ms": body.get("extra_wait_ms", config.close_modal_extra_wait_ms),
    }


@app.post("/workers/all/auth/phone")
async def all_workers_auth_phone(request: Request) -> dict[str, object]:
    return service.proxy_all_workers("/actions/auth/phone", _simple_phone_payload(await _request_json(request)))


@app.post("/workers/all/auth/code")
async def all_workers_auth_code(request: Request) -> dict[str, object]:
    return service.proxy_all_workers("/actions/auth/code", _simple_code_payload(await _request_json(request)))


@app.post("/workers/all/cabinet/switch")
async def all_workers_cabinet_switch(request: Request) -> dict[str, object]:
    return service.proxy_all_workers("/actions/hover-profile-menu-select", _cabinet_payload(await _request_json(request)))


@app.post("/workers/all/close-modal")
async def all_workers_close_modal(request: Request) -> dict[str, object]:
    return service.proxy_all_workers("/actions/click", _close_modal_payload(await _request_json(request)))


@app.post("/workers/all/session/start")
async def all_workers_session_start(request: Request) -> dict[str, object]:
    body = await _request_json(request)
    return service.proxy_all_workers("/session/start", body or {"restart_if_running": False})


@app.post("/workers/all/session/close")
def all_workers_session_close() -> dict[str, object]:
    return service.proxy_all_workers("/session/close", {})


@app.post("/workers/all/session/restart")
async def all_workers_session_restart(request: Request) -> dict[str, object]:
    body = await _request_json(request)
    body["restart_if_running"] = True
    return service.proxy_all_workers("/session/start", body)


@app.post("/workers/{worker_id}/auth/start")
async def worker_auth_start(worker_id: str, request: Request) -> dict[str, object]:
    body = await _request_json(request)
    return service.proxy_worker(worker_id, "/session/start", body or {"restart_if_running": False})


@app.post("/workers/{worker_id}/session/start")
async def worker_session_start(worker_id: str, request: Request) -> dict[str, object]:
    body = await _request_json(request)
    return service.proxy_worker(worker_id, "/session/start", body or {"restart_if_running": False})


@app.post("/workers/{worker_id}/session/close")
def worker_session_close(worker_id: str) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/session/close", {})


@app.post("/workers/{worker_id}/session/restart")
async def worker_session_restart(worker_id: str, request: Request) -> dict[str, object]:
    body = await _request_json(request)
    body["restart_if_running"] = True
    return service.proxy_worker(worker_id, "/session/start", body)


@app.post("/workers/{worker_id}/auth/phone")
async def worker_auth_phone_simple(worker_id: str, request: Request) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/actions/auth/phone", _simple_phone_payload(await _request_json(request)))


@app.post("/workers/{worker_id}/auth/code")
async def worker_auth_code_simple(worker_id: str, request: Request) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/actions/auth/code", _simple_code_payload(await _request_json(request)))


@app.post("/workers/{worker_id}/cabinet/switch")
async def worker_cabinet_switch(worker_id: str, request: Request) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/actions/hover-profile-menu-select", _cabinet_payload(await _request_json(request)))


@app.post("/workers/{worker_id}/close-modal")
async def worker_close_modal(worker_id: str, request: Request) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/actions/click", _close_modal_payload(await _request_json(request)))


@app.post("/workers/{worker_id}/actions/auth/phone")
async def worker_auth_phone(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/auth/phone", body)


@app.post("/workers/{worker_id}/actions/auth/code")
async def worker_auth_code(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/auth/code", body)


@app.post("/workers/{worker_id}/actions/navigate")
async def worker_navigate(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/navigate", body)


@app.post("/workers/{worker_id}/actions/fill")
async def worker_fill(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/fill", body)


@app.post("/workers/{worker_id}/actions/click")
async def worker_click(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/click", body)


@app.post("/workers/{worker_id}/actions/press")
async def worker_press(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/press", body)


@app.post("/workers/{worker_id}/actions/find-button-by-div-text")
async def worker_find_button_by_div_text(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/find-button-by-div-text", body)


@app.post("/workers/{worker_id}/actions/hover-profile-menu-select")
async def worker_hover_profile_menu_select(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/hover-profile-menu-select", body)


@app.post("/workers/{worker_id}/actions/click-anchor-by-span-text")
async def worker_click_anchor_by_span_text(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/click-anchor-by-span-text", body)


@app.post("/workers/{worker_id}/actions/snapshot")
async def worker_snapshot(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return service.proxy_worker(worker_id, "/actions/snapshot", body)


@app.post("/workers/{worker_id}/refresh")
async def worker_refresh(worker_id: str, request: Request) -> dict[str, object]:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    if not body:
        raise HTTPException(status_code=400, detail="Refresh payload must contain the old worker navigate request body.")
    return service.proxy_worker(worker_id, "/actions/navigate", body)


@app.post("/workers/{worker_id}/reset")
def worker_reset(worker_id: str) -> dict[str, object]:
    return service.proxy_worker(worker_id, "/session/close", {})


@app.post("/tasks/{task_id}/callback/retry")
def retry_callback(task_id: str) -> dict[str, object]:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] not in {"completed", "failed", "callback_pending", "callback_failed"}:
        raise HTTPException(status_code=409, detail="Task is not ready for callback retry.")
    service.deliver_callback(task_id)
    refreshed = store.get_task(task_id)
    return {"task_id": task_id, "status": refreshed["status"] if refreshed else "unknown"}


def main() -> int:
    uvicorn.run(app, host=config.api_host, port=config.api_port, log_level=config.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
