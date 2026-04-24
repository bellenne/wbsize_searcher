from __future__ import annotations

import logging
import sys

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
    SessionStartRequest,
    SnapshotRequest,
    WaitRequest,
)
from browser_service import BrowserSessionManager
from config import AppConfig


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
manager = BrowserSessionManager(config=config, bootstrap_logger=bootstrap_logger)
app = FastAPI(title="Playwright Browser Worker API", version="1.0.0")


def _handle_error(exc: Exception) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.on_event("shutdown")
def _shutdown() -> None:
    manager.close_session()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "api_host": config.api_host,
        "api_port": config.api_port,
        "session": manager.get_state(),
    }


@app.get("/session/state")
def session_state() -> dict[str, object]:
    return manager.get_state()


@app.post("/session/start")
def session_start(payload: SessionStartRequest) -> dict[str, object]:
    try:
        return manager.start_session(
            target_url=payload.target_url,
            restart_if_running=payload.restart_if_running,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/session/close")
def session_close() -> dict[str, object]:
    try:
        return manager.close_session()
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/navigate")
def action_navigate(payload: NavigateRequest) -> dict[str, object]:
    try:
        return manager.navigate(
            url=payload.url,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/wait")
def action_wait(payload: WaitRequest) -> dict[str, object]:
    try:
        return manager.wait_for_page_ready(
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
            timeout_ms=payload.timeout_ms,
            snapshot_name=payload.snapshot_name,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/fill")
def action_fill(payload: FillRequest) -> dict[str, object]:
    try:
        return manager.fill_selector(
            selector=payload.selector,
            value=payload.value,
            action_name=payload.action_name,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/click")
def action_click(payload: ClickRequest) -> dict[str, object]:
    try:
        return manager.click_selector(
            selector=payload.selector,
            action_name=payload.action_name,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/press")
def action_press(payload: PressRequest) -> dict[str, object]:
    try:
        return manager.press_selector(
            selector=payload.selector,
            key=payload.key,
            action_name=payload.action_name,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/auth/phone")
def action_auth_phone(payload: AuthPhoneRequest) -> dict[str, object]:
    try:
        return manager.submit_auth_phone(
            phone=payload.phone,
            selector=payload.selector,
            submit_selector=payload.submit_selector,
            wait_ms=payload.wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/auth/code")
def action_auth_code(payload: AuthCodeRequest) -> dict[str, object]:
    try:
        return manager.submit_auth_code(
            code=payload.code,
            selector=payload.selector,
            submit_selector=payload.submit_selector,
            wait_for_networkidle=payload.wait_for_networkidle,
            extra_wait_ms=payload.extra_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/find-button-by-div-text")
def action_find_button(payload: FindButtonByDivTextRequest) -> dict[str, object]:
    try:
        return manager.find_button_by_div_text(
            div_text=payload.div_text,
            click=payload.click,
            timeout_ms=payload.timeout_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/hover-profile-menu-select")
def action_hover_profile_menu_select(payload: HoverProfileMenuSelectRequest) -> dict[str, object]:
    try:
        return manager.hover_profile_menu_and_click_item(
            profile_selector=payload.profile_selector,
            current_button_text=payload.current_button_text,
            target_menu_text=payload.target_menu_text,
            timeout_ms=payload.timeout_ms,
            hover_wait_ms=payload.hover_wait_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/click-anchor-by-span-text")
def action_click_anchor_by_span_text(payload: ClickAnchorBySpanTextRequest) -> dict[str, object]:
    try:
        return manager.click_anchor_by_span_text(
            text=payload.text,
            scope_selector=payload.scope_selector,
            timeout_ms=payload.timeout_ms,
            post_click_wait_ms=payload.post_click_wait_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/search-text")
def action_search_text(payload: SearchTextRequest) -> dict[str, object]:
    try:
        return manager.search_text(payload.text)
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/extract-size-by-code")
def action_extract_size_by_code(payload: ExtractSizeByCodeRequest) -> dict[str, object]:
    try:
        return manager.extract_size_by_code(
            code=payload.code,
            row_selector=payload.row_selector,
            value_cell_selector=payload.value_cell_selector,
            size_regex=payload.size_regex,
            timeout_ms=payload.timeout_ms,
        )
    except Exception as exc:
        _handle_error(exc)


@app.post("/actions/snapshot")
def action_snapshot(payload: SnapshotRequest) -> dict[str, object]:
    try:
        return manager.save_snapshot(
            name=payload.name,
            save_html=payload.save_html,
            save_text=payload.save_text,
        )
    except Exception as exc:
        _handle_error(exc)


def main() -> int:
    bootstrap_logger.info("Starting HTTP API server on %s:%s", config.api_host, config.api_port)
    uvicorn.run(app, host=config.api_host, port=config.api_port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
