from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, HTTPException

from api_models import (
    AuthCodeRequest,
    AuthPhoneRequest,
    ClickRequest,
    ClickAnchorBySpanTextRequest,
    ExtractSizeByCodeRequest,
    FillRequest,
    FindButtonByDivTextRequest,
    HoverProfileMenuSelectRequest,
    NavigateRequest,
    PressRequest,
    SearchTextRequest,
    SearchSizeTaskRequest,
    SessionStartRequest,
    SnapshotRequest,
    WaitRequest,
)
from browser_service import BrowserSessionManager
from config import AppConfig
from scenario_config import ScenarioConfig
from thread_executor import ThreadExecutor


def _build_bootstrap_logger() -> logging.Logger:
    logger = logging.getLogger("browser_worker.bootstrap")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


bootstrap_logger = _build_bootstrap_logger()
bootstrap_logger.info("Application bootstrap started.")
config = AppConfig.from_env()
scenario_config = ScenarioConfig.from_app_config(config)
manager = BrowserSessionManager(config=config, bootstrap_logger=bootstrap_logger)
browser_executor = ThreadExecutor(name=f"{config.worker_id}-browser-thread")
app = FastAPI(title="Playwright Browser Worker API", version="1.0.0")


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _busy_error(task_id: str | None = None) -> dict[str, object]:
    return {
        "success": False,
        "task_id": task_id,
        "worker_id": config.worker_id,
        "error_code": "WORKER_BUSY",
        "message": "Worker is already executing a Playwright task.",
        "retryable": True,
    }


def _error_payload(
    *,
    task_id: str | None,
    external_task_id: str | None,
    order_number: str | None,
    error_code: str,
    message: str,
    retryable: bool,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": False,
        "task_id": task_id,
        "external_task_id": external_task_id,
        "worker_id": config.worker_id,
        "order_number": order_number,
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
    }
    if details:
        payload["details"] = details
    return payload


def _classify_error(exc: Exception) -> tuple[str, bool]:
    message = str(exc)
    lowered = message.lower()
    if "no active browser session" in lowered or "page" in lowered and "not ready" in lowered:
        return "PAGE_NOT_READY", True
    if "auth" in lowered and "required" in lowered:
        return "AUTH_REQUIRED", False
    if isinstance(exc, TimeoutError) and (
        "row by code was not found" in message
        or "size pattern was not found" in message
        or manager.last_action == "extract_size_by_code"
    ):
        return "SIZE_NOT_FOUND", True
    if "row by code was not found" in message or "size pattern was not found" in message:
        return "SIZE_NOT_FOUND", True
    if "timeout" in lowered or "timed out" in lowered:
        if manager.last_action in {"navigate", "wait_for_page_ready"}:
            return "NAVIGATION_TIMEOUT", True
        return "SIZE_NOT_FOUND", True
    if "browser" in lowered or "playwright" in lowered or "target page" in lowered:
        return "BROWSER_ERROR", True
    return "BROWSER_ERROR", True


def _run_exclusive(action_name: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    task_id = f"legacy:{action_name}"
    if not manager.try_begin_task(task_id):
        raise HTTPException(status_code=409, detail=_busy_error(task_id))
    try:
        def execute() -> dict[str, Any]:
            result = callback()
            manager.finish_task(success=True)
            return result

        return browser_executor.run(execute)
    except Exception as exc:
        error_code, _retryable = _classify_error(exc)
        browser_executor.run(
            lambda: manager.finish_task(success=False, error_code=error_code if error_code == "AUTH_REQUIRED" else None)
        )
        _handle_error(exc)


@app.on_event("shutdown")
def _shutdown() -> None:
    browser_executor.run(manager.close_session)


@app.on_event("startup")
def _startup() -> None:
    if not config.auto_start_session:
        return
    browser_executor.submit(_auto_start_session)


def _auto_start_session() -> None:
    target_url = config.auto_start_target_url or scenario_config.workspace.url
    bootstrap_logger.info(
        "Auto-starting browser session. worker_id=%s target_url=%s",
        config.worker_id,
        target_url,
    )
    if not manager.try_begin_task("startup:auto_start_session"):
        bootstrap_logger.warning("Auto-start skipped because worker is busy. worker_id=%s", config.worker_id)
        return
    try:
        manager.start_session(target_url=target_url, restart_if_running=False, wait_for_networkidle=False)
        manager.refresh_authorization_status_from_url()
        bootstrap_logger.info(
            "Auto-start completed. worker_id=%s authorized=%s status=%s",
            config.worker_id,
            manager.authorized,
            manager.get_worker_status().get("status"),
        )
        manager.finish_task(success=True)
    except Exception as exc:
        bootstrap_logger.exception("Auto-start failed. worker_id=%s", config.worker_id)
        manager.last_error = str(exc)
        if manager.is_session_active():
            manager.refresh_authorization_status_from_url()
            manager.finish_task(success=True)
        else:
            manager.finish_task(success=False, error_code="BROWSER_ERROR")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "alive": True,
        "worker_id": config.worker_id,
        "timestamp": _utc_now(),
        "status": "ok",
        "api_host": config.api_host,
        "api_port": config.api_port,
        "session": browser_executor.run(manager.get_state),
    }


@app.get("/status")
def status() -> dict[str, object]:
    return browser_executor.run(manager.get_worker_status)


@app.get("/session/state")
def session_state() -> dict[str, object]:
    return browser_executor.run(manager.get_state)


@app.post("/session/start")
def session_start(payload: SessionStartRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "session_start",
            lambda: manager.start_session(
            target_url=payload.target_url,
            restart_if_running=payload.restart_if_running,
            wait_for_networkidle=False,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/session/close")
def session_close() -> dict[str, object]:
    try:
        return _run_exclusive("session_close", manager.close_session)
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/navigate")
def action_navigate(payload: NavigateRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "navigate",
            lambda: manager.navigate(
            url=payload.url,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/wait")
def action_wait(payload: WaitRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "wait",
            lambda: manager.wait_for_page_ready(
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
            timeout_ms=payload.timeout_ms,
            snapshot_name=payload.snapshot_name,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/fill")
def action_fill(payload: FillRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "fill",
            lambda: manager.fill_selector(
            selector=payload.selector,
            value=payload.value,
            action_name=payload.action_name,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/click")
def action_click(payload: ClickRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "click",
            lambda: manager.click_selector(
            selector=payload.selector,
            action_name=payload.action_name,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/press")
def action_press(payload: PressRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "press",
            lambda: manager.press_selector(
            selector=payload.selector,
            key=payload.key,
            action_name=payload.action_name,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/auth/phone")
def action_auth_phone(payload: AuthPhoneRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "auth_phone",
            lambda: manager.submit_auth_phone(
            phone=payload.phone,
            selector=payload.selector,
            submit_selector=payload.submit_selector,
            wait_ms=payload.wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/auth/code")
def action_auth_code(payload: AuthCodeRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "auth_code",
            lambda: manager.submit_auth_code(
            code=payload.code,
            selector=payload.selector,
            submit_selector=payload.submit_selector,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/find-button-by-div-text")
def action_find_button(payload: FindButtonByDivTextRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "find_button_by_div_text",
            lambda: manager.find_button_by_div_text(
            div_text=payload.div_text,
            click=payload.click,
            timeout_ms=payload.timeout_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/hover-profile-menu-select")
def action_hover_profile_menu_select(payload: HoverProfileMenuSelectRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "hover_profile_menu_select",
            lambda: manager.hover_profile_menu_and_click_item(
            profile_selector=payload.profile_selector,
            current_button_text=payload.current_button_text,
            target_menu_text=payload.target_menu_text,
            timeout_ms=payload.timeout_ms,
            hover_wait_ms=payload.hover_wait_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/click-anchor-by-span-text")
def action_click_anchor_by_span_text(payload: ClickAnchorBySpanTextRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "click_anchor_by_span_text",
            lambda: manager.click_anchor_by_span_text(
            text=payload.text,
            scope_selector=payload.scope_selector,
            timeout_ms=payload.timeout_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/search-text")
def action_search_text(payload: SearchTextRequest) -> dict[str, object]:
    try:
        return _run_exclusive("search_text", lambda: manager.search_text(payload.text))
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/extract-size-by-code")
def action_extract_size_by_code(payload: ExtractSizeByCodeRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "extract_size_by_code",
            lambda: manager.extract_size_by_code(
            code=payload.code,
            row_selector=payload.row_selector,
            value_cell_selector=payload.value_cell_selector,
            size_regex=payload.size_regex,
            timeout_ms=payload.timeout_ms,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/snapshot")
def action_snapshot(payload: SnapshotRequest) -> dict[str, object]:
    try:
        return _run_exclusive(
            "snapshot",
            lambda: manager.save_snapshot(
            name=payload.name,
            save_html=payload.save_html,
            save_text=payload.save_text,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/task/search-size")
def task_search_size(payload: SearchSizeTaskRequest) -> dict[str, object]:
    order_number = (payload.order_number or payload.code or "").strip()
    attempt = payload.attempt or 1
    if not payload.task_id.strip() or not order_number:
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                task_id=payload.task_id,
                external_task_id=payload.external_task_id,
                order_number=order_number or None,
                error_code="INVALID_INPUT",
                message="task_id and order_number are required.",
                retryable=False,
            ),
        )

    if not manager.try_begin_task(payload.task_id):
        raise HTTPException(status_code=409, detail=_busy_error(payload.task_id))

    started = perf_counter()
    return browser_executor.run(lambda: _execute_search_size_task(payload, order_number, attempt, started))


def _execute_search_size_task(
    payload: SearchSizeTaskRequest,
    order_number: str,
    attempt: int,
    started: float,
) -> dict[str, object]:
    bootstrap_logger.info(
        "Задача поиска размера: старт. worker_id=%s task_id=%s order_number=%s force_refresh=%s close_modal=%s attempt=%s",
        config.worker_id,
        payload.task_id,
        order_number,
        payload.force_refresh,
        payload.close_modal,
        attempt,
    )
    try:
        if not manager.is_session_active():
            raise RuntimeError("Page is not ready: no active browser session.")
        manager.refresh_authorization_status_from_url()
        if not manager.authorized:
            raise RuntimeError("AUTH_REQUIRED")

        if payload.force_refresh:
            bootstrap_logger.info(
                "Задача поиска размера: обновляю рабочую страницу. worker_id=%s task_id=%s url=%s wait_ms=%s",
                config.worker_id,
                payload.task_id,
                scenario_config.workspace.url,
                scenario_config.workspace.extra_wait_ms,
            )
            manager.navigate(
                scenario_config.workspace.url,
                wait_for_networkidle=False,
                extra_wait_ms=scenario_config.workspace.extra_wait_ms,
            )

        if payload.close_modal:
            try:
                if scenario_config.close_modal.selector:
                    bootstrap_logger.info(
                        "Задача поиска размера: закрываю модалку. worker_id=%s task_id=%s selector=%s",
                        config.worker_id,
                        payload.task_id,
                        scenario_config.close_modal.selector,
                    )
                    manager.click_selector(
                        scenario_config.close_modal.selector,
                        action_name="close modal",
                        wait_for_networkidle=False,
                        extra_wait_ms=scenario_config.close_modal.click_wait_ms,
                    )
                elif scenario_config.close_modal.div_text:
                    manager.find_button_by_div_text(
                        div_text=scenario_config.close_modal.div_text,
                        click=True,
                        post_click_wait_ms=scenario_config.close_modal.click_wait_ms,
                    )
            except Exception as exc:
                bootstrap_logger.info(
                    "Задача поиска размера: модалку не закрыл, продолжаю. worker_id=%s task_id=%s message=%s",
                    config.worker_id,
                    payload.task_id,
                    exc,
                )

        bootstrap_logger.info(
            "Задача поиска размера: ищу размер по номеру заказа. worker_id=%s task_id=%s order_number=%s regex=%s",
            config.worker_id,
            payload.task_id,
            order_number,
            scenario_config.search_size.size_regex,
        )
        result = manager.extract_size_by_code(
            code=order_number,
            row_selector=scenario_config.search_size.row_selector,
            value_cell_selector=scenario_config.search_size.value_cell_selector,
            size_regex=scenario_config.search_size.size_regex,
            timeout_ms=scenario_config.search_size.timeout_ms,
        )
        duration_ms = int((perf_counter() - started) * 1000)
        response: dict[str, object] = {
            "success": True,
            "task_id": payload.task_id,
            "external_task_id": payload.external_task_id,
            "worker_id": config.worker_id,
            "order_number": order_number,
            "size": result.get("size"),
            "attempt": attempt,
            "duration_ms": duration_ms,
        }
        if payload.metadata:
            response["metadata"] = payload.metadata
        bootstrap_logger.info(
            "Задача поиска размера: успех. worker_id=%s task_id=%s order_number=%s size=%s duration_ms=%s",
            config.worker_id,
            payload.task_id,
            order_number,
            result.get("size"),
            duration_ms,
        )
        manager.finish_task(success=True)
        return response
    except Exception as exc:
        error_code, retryable = _classify_error(exc)
        duration_ms = int((perf_counter() - started) * 1000)
        bootstrap_logger.exception(
            "Задача поиска размера: ошибка. worker_id=%s task_id=%s order_number=%s error_code=%s duration_ms=%s",
            config.worker_id,
            payload.task_id,
            order_number,
            error_code,
            duration_ms,
        )
        manager.last_error = str(exc)
        manager.finish_task(success=False, error_code=error_code)
        return _error_payload(
            task_id=payload.task_id,
            external_task_id=payload.external_task_id,
            order_number=order_number,
            error_code=error_code,
            message=str(exc),
            retryable=retryable,
            details={"duration_ms": duration_ms, "attempt": attempt},
        )


def main() -> int:
    bootstrap_logger.info("Starting HTTP API server on %s:%s", config.api_host, config.api_port)
    uvicorn.run(app, host=config.api_host, port=config.api_port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
