from __future__ import annotations

import select
import signal
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from time import sleep
from time import perf_counter
from time import time
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Dialog,
    Error,
    Page,
    Playwright,
    Request,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config import AppConfig
from logger_setup import LoggerSet
from utils import (
    build_artifact_path,
    find_text_occurrences,
    write_json_file,
    write_text_file,
)


@dataclass(slots=True)
class ScenarioResult:
    target_url: str
    search_text: str
    found: bool
    matches_count: int
    run_timestamp: str
    duration_ms: int
    auth_attempted: bool = False
    auth_completed: bool = False
    target_button_text: str | None = None
    target_button_found: bool = False
    target_button_clicked: bool = False
    error: str | None = None


class BrowserRunner:
    """Оркестратор базового Playwright-сценария с подробным логированием."""

    def __init__(
        self,
        config: AppConfig,
        run_directory: Path,
        run_timestamp: str,
        loggers: LoggerSet,
    ) -> None:
        self.config = config
        self.run_directory = run_directory
        self.run_timestamp = run_timestamp
        self.loggers = loggers
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.trace_started = False
        self.started_at = perf_counter()
        self.stop_requested = False
        self.result = ScenarioResult(
            target_url=config.target_url,
            search_text=config.search_text,
            found=False,
            matches_count=0,
            run_timestamp=run_timestamp,
            duration_ms=0,
            target_button_text=config.target_button_div_text,
            error=None,
        )
        self._register_signal_handlers()

    def run(self) -> dict[str, Any]:
        # Центральный сценарий держим линейным, чтобы его было проще расширять новыми шагами.
        self.loggers.app.info("Base scenario started.")

        try:
            self.initialize_browser()
            self.navigate()
            self.perform_auth_flow()
            self.find_target_button()
            page_text = self.extract_visible_text()
            self.perform_search(page_text)
        except Exception as exc:
            self.result.error = str(exc)
            self.loggers.app.exception("Unhandled exception during browser scenario.")
            self._save_exception_trace(exc)
        finally:
            self.result.duration_ms = int((perf_counter() - self.started_at) * 1000)
            self._save_result_json()
            if self.config.keep_session_alive and self.browser is not None and self.context is not None:
                self.idle_with_open_session()
            self.finalize()

        self.loggers.app.info("Base scenario finished.")
        return asdict(self.result)

    def initialize_browser(self) -> None:
        # Запуск браузера и контекста вынесен отдельно, чтобы далее было удобно добавлять auth,
        # proxy, cookies, HAR и другие расширения без переписывания run().
        self.loggers.app.info("Starting Playwright runtime.")
        self.playwright = sync_playwright().start()
        self.loggers.app.info(
            "Launching Chromium. headless=%s timeout_ms=%s",
            self.config.headless,
            self.config.browser_timeout_ms,
        )
        self.browser = self.playwright.chromium.launch(
            headless=self.config.headless,
            args=["--disable-dev-shm-usage"],
        )
        self.loggers.app.info("Chromium started successfully.")

        context_options: dict[str, Any] = {
            "viewport": {
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            }
        }
        if self.config.user_agent:
            context_options["user_agent"] = self.config.user_agent

        self.loggers.app.info("Creating browser context with options: %s", context_options)
        self.context = self.browser.new_context(**context_options)
        self.context.set_default_timeout(self.config.browser_timeout_ms)
        self.context.set_default_navigation_timeout(self.config.browser_timeout_ms)
        self.loggers.app.info("Browser context created.")

        self.loggers.app.info("Starting Playwright trace recording.")
        self.context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
            title=f"browser_run_{self.run_timestamp}",
        )
        self.trace_started = True
        self.loggers.app.info("Playwright tracing is enabled.")

        self.loggers.app.info("Creating page instance.")
        self.page = self.context.new_page()
        self.loggers.app.info("Page instance created.")

        # Сначала подписываемся на события, и только потом идем в сеть, чтобы не потерять ранние логи.
        self.subscribe_page_events()
        self.save_screenshot("001", "browser_started")
        self.save_screenshot("002", "before_goto")

    def subscribe_page_events(self) -> None:
        if self.page is None or self.context is None:
            raise RuntimeError("Cannot subscribe to events before page/context creation.")

        self.loggers.app.info("Subscribing to page and context events.")
        self.page.on("console", self._on_console_message)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("dialog", self._on_dialog)
        self.page.on("domcontentloaded", self._on_dom_content_loaded)
        self.page.on("load", self._on_page_load)
        self.page.on("framenavigated", self._on_frame_navigated)

        self.context.on("request", self._on_request)
        self.context.on("response", self._on_response)
        self.context.on("requestfailed", self._on_request_failed)
        self.context.on("weberror", self._on_web_error)
        self.loggers.app.info("Event subscriptions completed.")

    def navigate(self) -> None:
        if self.page is None:
            raise RuntimeError("Page is not initialized.")

        # Идём до DOMContentLoaded, а затем отдельным явным шагом ждём networkidle.
        # Так в логах и артефактах лучше видно, на каком этапе возникла проблема.
        self.loggers.app.info("Navigating to target URL: %s", self.config.target_url)
        try:
            response = self.page.goto(
                self.config.target_url,
                wait_until="domcontentloaded",
                timeout=self.config.browser_timeout_ms,
            )
        except PlaywrightTimeoutError:
            self.loggers.app.exception("Timeout while navigating to %s", self.config.target_url)
            raise

        if response is None:
            self.loggers.app.warning("Navigation returned no response object.")
        else:
            self.loggers.app.info(
                "Navigation completed. final_url=%s status=%s",
                self.page.url,
                response.status,
            )

        self.save_screenshot("003", "after_goto")
        self.loggers.app.info("Waiting for networkidle state.")
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.config.browser_timeout_ms)
        except PlaywrightTimeoutError:
            self.loggers.app.exception("Timeout while waiting for networkidle.")
            raise

        self.loggers.app.info("networkidle state reached.")

        if self.config.extra_wait_ms > 0:
            self.loggers.app.info(
                "Waiting extra %s ms for dynamic content stabilization.",
                self.config.extra_wait_ms,
            )
            self.page.wait_for_timeout(self.config.extra_wait_ms)
            self.loggers.app.info("Extra wait finished.")

        self.save_screenshot("004", "after_networkidle")

        if self.config.save_html:
            self.save_html("004", "page_source")

    def extract_visible_text(self) -> str:
        if self.page is None:
            raise RuntimeError("Page is not initialized.")

        # inner_text() даёт близкий к "видимому" тексту результат, что полезнее для поиска,
        # чем сырой HTML. При сбое остаётся JS fallback.
        self.loggers.app.info("Extracting visible text from page.")
        try:
            body = self.page.locator("body")
            page_text = body.inner_text(timeout=self.config.browser_timeout_ms)
        except Exception:
            self.loggers.app.warning(
                "Failed to extract body.inner_text(). Falling back to document.body.innerText.",
                exc_info=True,
            )
            page_text = self.page.evaluate("document.body ? document.body.innerText : ''")

        page_text = page_text.strip()
        self.loggers.app.info("Visible text extracted. characters=%s", len(page_text))

        if self.config.save_text:
            self.save_page_text("020", "visible_text", page_text)

        return page_text

    def perform_search(self, page_text: str) -> None:
        # Поиск вынесен в отдельный шаг, чтобы сюда позже можно было добавить regex,
        # fuzzy matching, фильтры по блокам страницы или поиск по селекторам.
        self.loggers.app.info("Starting case-insensitive text search. search_text=%r", self.config.search_text)
        found, matches_count, positions = find_text_occurrences(page_text, self.config.search_text)
        self.result.found = found
        self.result.matches_count = matches_count

        self.loggers.app.info(
            "Search finished. found=%s matches_count=%s positions=%s",
            found,
            matches_count,
            positions[:20],
        )
        self.save_screenshot("020", "after_search")

    def perform_auth_flow(self) -> None:
        if not self.config.auth_enabled:
            self.loggers.app.info("Authentication flow is disabled. Skipping auth step.")
            return
        if self.page is None:
            raise RuntimeError("Page is not initialized.")

        self.result.auth_attempted = True
        self.loggers.app.info("Authentication flow started.")

        phone_field = self.fill_selector(
            selector=self.config.auth_phone_selector,
            value=self.config.auth_phone or "",
            action_name="phone input field",
        )
        self.save_screenshot("010", "auth_phone_filled")

        self.submit_step(
            selector=self.config.auth_phone_submit_selector,
            fallback_locator=phone_field,
            action_name="phone submit action",
        )
        self.save_screenshot("011", "auth_phone_submitted")
        self.wait_after_action(
            self.config.auth_wait_after_phone_submit_ms,
            "waiting after phone submit",
            "012",
            "after_phone_submit_wait",
        )

        auth_code = self.resolve_auth_code()
        code_field = self.fill_selector(
            selector=self.config.auth_code_selector,
            value=auth_code,
            action_name="auth code input field",
        )
        self.save_screenshot("013", "auth_code_filled")

        self.submit_step(
            selector=self.config.auth_code_submit_selector,
            fallback_locator=code_field,
            action_name="auth code submit action",
        )
        self.save_screenshot("014", "auth_code_submitted")
        self.wait_after_action(
            self.config.auth_wait_after_code_submit_ms,
            "waiting after code submit",
            "015",
            "after_code_submit_wait",
        )
        self.wait_for_post_auth_page_ready()

        self.result.auth_completed = True
        self.loggers.app.info("Authentication flow completed.")

    def wait_for_post_auth_page_ready(self) -> None:
        if self.page is None:
            raise RuntimeError("Page is not initialized.")

        self.loggers.app.info("Post-auth page readiness check started.")
        if self.config.post_auth_wait_for_networkidle:
            self.loggers.app.info("Waiting for networkidle after auth.")
            self.page.wait_for_load_state("networkidle", timeout=self.config.browser_timeout_ms)
            self.loggers.app.info("Post-auth networkidle reached.")
        else:
            self.loggers.app.info("POST_AUTH_WAIT_FOR_NETWORKIDLE=false. Skipping networkidle wait.")

        if self.config.post_auth_extra_wait_ms > 0:
            self.loggers.app.info(
                "Waiting additional %s ms after auth page load.",
                self.config.post_auth_extra_wait_ms,
            )
            self.page.wait_for_timeout(self.config.post_auth_extra_wait_ms)
            self.loggers.app.info("Post-auth extra wait finished.")

        self.save_screenshot("016", "after_post_auth_ready")
        if self.config.save_html:
            self.save_html("016", "post_auth_page_source")

    def find_target_button(self) -> None:
        if self.page is None:
            raise RuntimeError("Page is not initialized.")
        if not self.config.target_button_div_text:
            self.loggers.app.info("TARGET_BUTTON_DIV_TEXT is empty. Skipping target button lookup.")
            return

        button_locator = self.page.locator(
            "button",
            has=self.page.locator("div", has_text=self.config.target_button_div_text),
        ).first

        self.loggers.app.info(
            "Waiting for target button containing child div text. text=%r timeout_ms=%s",
            self.config.target_button_div_text,
            self.config.target_button_timeout_ms,
        )
        button_locator.wait_for(state="visible", timeout=self.config.target_button_timeout_ms)
        self.result.target_button_found = True
        self.loggers.app.info("Target button located successfully. text=%r", self.config.target_button_div_text)
        self.save_screenshot("017", "target_button_found")

        if not self.config.click_target_button:
            self.loggers.app.info("CLICK_TARGET_BUTTON=false. Button click skipped.")
            return

        self.loggers.app.info("Clicking target button. text=%r", self.config.target_button_div_text)
        button_locator.click(timeout=self.config.target_button_timeout_ms)
        self.result.target_button_clicked = True
        self.loggers.app.info("Target button click completed.")
        self.save_screenshot("018", "after_target_button_click")
        self.wait_after_action(
            self.config.target_button_click_wait_ms,
            "waiting after target button click",
            "019",
            "after_target_button_click_wait",
        )

    def wait_for_selector(self, selector: str | None, action_name: str):
        if self.page is None:
            raise RuntimeError("Page is not initialized.")
        if not selector:
            raise ValueError(f"Selector is required for {action_name}.")

        self.loggers.app.info("Waiting for selector. action=%s selector=%s", action_name, selector)
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible", timeout=self.config.browser_timeout_ms)
        self.loggers.app.info("Selector is visible. action=%s selector=%s", action_name, selector)
        return locator

    def click_selector(self, selector: str, action_name: str) -> None:
        locator = self.wait_for_selector(selector, action_name)
        self.loggers.app.info("Clicking selector. action=%s selector=%s", action_name, selector)
        locator.click(timeout=self.config.browser_timeout_ms)
        self.loggers.app.info("Click completed. action=%s selector=%s", action_name, selector)

    def fill_selector(self, selector: str | None, value: str, action_name: str):
        locator = self.wait_for_selector(selector, action_name)
        self.loggers.app.info(
            "Filling selector. action=%s selector=%s value_length=%s",
            action_name,
            selector,
            len(value),
        )
        locator.fill("")
        locator.fill(value, timeout=self.config.browser_timeout_ms)
        self.loggers.app.info("Fill completed. action=%s selector=%s", action_name, selector)
        return locator

    def submit_step(self, selector: str | None, fallback_locator: Any, action_name: str) -> None:
        if selector:
            self.click_selector(selector, action_name)
            return

        self.loggers.app.info(
            "No explicit submit selector configured. Using Enter key fallback. action=%s",
            action_name,
        )
        fallback_locator.press("Enter", timeout=self.config.browser_timeout_ms)
        self.loggers.app.info("Enter key submit completed. action=%s", action_name)

    def wait_after_action(
        self,
        wait_ms: int,
        action_name: str,
        step_prefix: str,
        screenshot_name: str,
    ) -> None:
        if self.page is None:
            raise RuntimeError("Page is not initialized.")
        if wait_ms <= 0:
            self.loggers.app.info("No additional wait configured. action=%s", action_name)
            return

        self.loggers.app.info("Waiting %s ms. action=%s", wait_ms, action_name)
        self.page.wait_for_timeout(wait_ms)
        self.loggers.app.info("Wait completed. action=%s", action_name)
        self.save_screenshot(step_prefix, screenshot_name)

    def resolve_auth_code(self) -> str:
        direct_code = self._resolve_auth_code_from_env()
        if direct_code:
            self.loggers.app.info("Using confirmation code from AUTH_CODE env.")
            return direct_code

        wait_started_at = time()
        deadline = perf_counter() + (self.config.auth_code_timeout_ms / 1000)
        prompt_written = False

        self.loggers.app.info(
            "Waiting for confirmation code. timeout_ms=%s file=%s interactive=%s",
            self.config.auth_code_timeout_ms,
            self.config.auth_code_file,
            self.config.auth_interactive_code_entry,
        )

        while perf_counter() < deadline:
            file_code = self._try_read_auth_code_file(wait_started_at)
            if file_code:
                self.loggers.app.info("Using confirmation code from AUTH_CODE_FILE.")
                return file_code

            interactive_code, prompt_written = self._try_read_auth_code_interactive(prompt_written)
            if interactive_code:
                self.loggers.app.info("Using confirmation code entered interactively.")
                return interactive_code

            sleep(self.config.auth_code_poll_interval_ms / 1000)

        raise TimeoutError("Confirmation code was not provided by env, file, or interactive input in time.")

    def _resolve_auth_code_from_env(self) -> str | None:
        if self.config.auth_code:
            return self.config.auth_code.strip()
        return None

    def _try_read_auth_code_file(self, wait_started_at: float) -> str | None:
        if self.config.auth_code_file is None:
            return None

        if not self.config.auth_code_file.exists():
            return None

        try:
            if self.config.auth_code_file.stat().st_mtime < wait_started_at:
                return None
            raw_code = self.config.auth_code_file.read_text(encoding="utf-8").strip()
        except Exception:
            self.loggers.app.exception(
                "Failed to read confirmation code file. path=%s",
                self.config.auth_code_file,
            )
            return None

        if raw_code:
            self.loggers.app.info("Confirmation code file detected and contains data.")
            return raw_code
        return None

    def _try_read_auth_code_interactive(self, prompt_written: bool) -> tuple[str | None, bool]:
        if not self.config.auth_interactive_code_entry:
            return None, prompt_written
        if not sys.stdin or sys.stdin.closed:
            if not prompt_written:
                self.loggers.app.warning("stdin is unavailable. Interactive confirmation code entry skipped.")
            return None, prompt_written

        if not prompt_written:
            self.loggers.app.info("Interactive confirmation code entry is enabled. Waiting for stdin input.")
            print("Enter confirmation code and press Enter:", flush=True)
            prompt_written = True

        try:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
        except Exception:
            self.loggers.app.exception("Failed to poll stdin for interactive confirmation code entry.")
            return None, prompt_written

        if not readable:
            return None, prompt_written

        raw_value = sys.stdin.readline()
        code = raw_value.strip()
        if not code:
            self.loggers.app.warning("Interactive confirmation code entry returned an empty value.")
            return None, prompt_written
        return code, prompt_written

    def save_screenshot(self, step_prefix: str, step_name: str) -> Path | None:
        if self.page is None:
            self.loggers.app.warning(
                "Screenshot was skipped because page is not initialized. step=%s_%s",
                step_prefix,
                step_name,
            )
            return None

        artifact_path = build_artifact_path(self.run_directory, step_prefix, step_name, "png")
        self.loggers.app.info("Saving screenshot to %s", artifact_path)
        try:
            self.page.screenshot(
                path=str(artifact_path),
                full_page=self.config.screenshot_full_page,
            )
        except Exception:
            self.loggers.app.exception("Failed to save screenshot: %s", artifact_path)
            return None

        self.loggers.app.info("Screenshot saved: %s", artifact_path)
        return artifact_path

    def save_html(self, step_prefix: str, step_name: str) -> Path:
        if self.page is None:
            raise RuntimeError("Cannot save HTML without a page.")

        artifact_path = build_artifact_path(self.run_directory, step_prefix, step_name, "html")
        self.loggers.app.info("Saving page HTML to %s", artifact_path)
        html = self.page.content()
        write_text_file(artifact_path, html)
        self.loggers.app.info("HTML saved: %s", artifact_path)
        return artifact_path

    def save_page_text(self, step_prefix: str, step_name: str, page_text: str) -> Path:
        artifact_path = build_artifact_path(self.run_directory, step_prefix, step_name, "txt")
        self.loggers.app.info("Saving visible page text to %s", artifact_path)
        write_text_file(artifact_path, page_text)
        self.loggers.app.info("Text dump saved: %s", artifact_path)
        return artifact_path

    def _save_result_json(self) -> Path:
        artifact_path = build_artifact_path(self.run_directory, "020", "search_result", "json")
        self.loggers.app.info("Saving search result JSON to %s", artifact_path)
        write_json_file(artifact_path, asdict(self.result))
        self.loggers.app.info("Search result JSON saved: %s", artifact_path)
        return artifact_path

    def idle_with_open_session(self) -> None:
        self.loggers.app.info(
            "KEEP_SESSION_ALIVE=true. Browser session will remain open until the container is stopped."
        )
        interval_seconds = self.config.keep_alive_log_interval_ms / 1000
        while not self.stop_requested:
            self.loggers.app.info(
                "Keep-alive heartbeat. Browser remains open. current_url=%s",
                self.page.url if self.page is not None else "<page-closed>",
            )
            sleep(interval_seconds)
        self.loggers.app.info("Keep-alive loop stopped. Proceeding with graceful shutdown.")

    def _save_exception_trace(self, exc: BaseException) -> None:
        artifact_path = build_artifact_path(self.run_directory, "900", "exception_traceback", "txt")
        self.loggers.app.info("Saving exception traceback to %s", artifact_path)
        content = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_text_file(artifact_path, content)
        self.loggers.app.info("Exception traceback saved: %s", artifact_path)

    def _save_storage_state(self) -> Path | None:
        if self.context is None:
            self.loggers.app.warning("Storage state save skipped because context is not initialized.")
            return None

        # Storage state полезен при отладке логина, cookies и локального хранилища.
        artifact_path = build_artifact_path(self.run_directory, "998", "storage_state", "json")
        self.loggers.app.info("Saving storage state to %s", artifact_path)
        try:
            self.context.storage_state(path=str(artifact_path))
        except Exception:
            self.loggers.app.exception("Failed to save storage state.")
            return None

        self.loggers.app.info("Storage state saved: %s", artifact_path)
        return artifact_path

    def _save_trace(self) -> Path | None:
        if self.context is None or not self.trace_started:
            self.loggers.app.warning("Trace save skipped because tracing is not active.")
            return None

        # Trace сохраняется до закрытия context, иначе Playwright уже не сможет его выгрузить.
        artifact_path = build_artifact_path(self.run_directory, "998", "playwright_trace", "zip")
        self.loggers.app.info("Stopping tracing and saving trace to %s", artifact_path)
        try:
            self.context.tracing.stop(path=str(artifact_path))
        except Exception:
            self.loggers.app.exception("Failed to save Playwright trace.")
            return None

        self.trace_started = False
        self.loggers.app.info("Playwright trace saved: %s", artifact_path)
        return artifact_path

    def finalize(self) -> None:
        # Финализация максимально defensive: даже при частично сломанном run()
        # мы всё равно пытаемся сохранить финальные артефакты и корректно закрыть ресурсы.
        self.loggers.app.info("Starting graceful shutdown sequence.")

        try:
            self.save_screenshot("999", "final")
        except Exception:
            self.loggers.app.exception("Failed to save final screenshot during shutdown.")

        self._save_storage_state()
        self._save_trace()

        if self.page is not None:
            try:
                self.loggers.app.info("Closing page.")
                self.page.close()
                self.loggers.app.info("Page closed.")
            except Exception:
                self.loggers.app.exception("Failed to close page cleanly.")

        if self.context is not None:
            try:
                self.loggers.app.info("Closing browser context.")
                self.context.close()
                self.loggers.app.info("Browser context closed.")
            except Exception:
                self.loggers.app.exception("Failed to close browser context cleanly.")

        if self.browser is not None:
            try:
                self.loggers.app.info("Closing browser.")
                self.browser.close()
                self.loggers.app.info("Browser closed.")
            except Exception:
                self.loggers.app.exception("Failed to close browser cleanly.")

        if self.playwright is not None:
            try:
                self.loggers.app.info("Stopping Playwright runtime.")
                self.playwright.stop()
                self.loggers.app.info("Playwright runtime stopped.")
            except Exception:
                self.loggers.app.exception("Failed to stop Playwright cleanly.")

    def _register_signal_handlers(self) -> None:
        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is None:
                continue
            signal.signal(signal_value, self._handle_stop_signal)

    def _handle_stop_signal(self, signum: int, _frame: Any) -> None:
        self.stop_requested = True
        self.loggers.app.info("Stop signal received. signum=%s", signum)

    def _on_console_message(self, message: ConsoleMessage) -> None:
        location = message.location
        self.loggers.console.info(
            "console type=%s text=%s url=%s line=%s column=%s",
            message.type,
            message.text,
            location.get("url", ""),
            location.get("lineNumber", ""),
            location.get("columnNumber", ""),
        )

    def _on_page_error(self, error: Error) -> None:
        self.loggers.errors.error("pageerror message=%s", error.message)
        self.loggers.app.warning("Page emitted pageerror. message=%s", error.message)

    def _on_dialog(self, dialog: Dialog) -> None:
        self.loggers.errors.warning(
            "dialog type=%s message=%s default_value=%s",
            dialog.type,
            dialog.message,
            dialog.default_value,
        )
        self.loggers.app.warning("Dialog detected. type=%s message=%s", dialog.type, dialog.message)
        try:
            dialog.dismiss()
            self.loggers.app.info("Dialog dismissed automatically.")
        except Exception:
            self.loggers.app.exception("Failed to dismiss dialog.")

    def _on_dom_content_loaded(self) -> None:
        current_url = self.page.url if self.page is not None else "<page-not-ready>"
        self.loggers.app.info("Page event received: domcontentloaded. url=%s", current_url)

    def _on_page_load(self) -> None:
        current_url = self.page.url if self.page is not None else "<page-not-ready>"
        self.loggers.app.info("Page event received: load. url=%s", current_url)

    def _on_frame_navigated(self, frame: Any) -> None:
        self.loggers.app.info(
            "Frame navigated. name=%s url=%s is_main=%s",
            frame.name,
            frame.url,
            frame == (self.page.main_frame if self.page is not None else None),
        )

    def _on_request(self, request: Request) -> None:
        redirected_from = request.redirected_from.url if request.redirected_from else ""
        # redirected_from помогает быстро увидеть серверные redirect-цепочки в логе.
        self.loggers.network.info(
            "request method=%s resource_type=%s is_navigation=%s url=%s redirected_from=%s",
            request.method,
            request.resource_type,
            request.is_navigation_request(),
            request.url,
            redirected_from,
        )

    def _on_response(self, response: Response) -> None:
        request = response.request
        redirect_marker = " redirect_response=true" if response.status in {301, 302, 303, 307, 308} else ""
        self.loggers.network.info(
            "response status=%s method=%s url=%s resource_type=%s%s",
            response.status,
            request.method,
            response.url,
            request.resource_type,
            redirect_marker,
        )

    def _on_request_failed(self, request: Request) -> None:
        self.loggers.errors.error(
            "requestfailed method=%s resource_type=%s url=%s failure=%s",
            request.method,
            request.resource_type,
            request.url,
            request.failure,
        )
        self.loggers.network.warning(
            "requestfailed method=%s resource_type=%s url=%s failure=%s",
            request.method,
            request.resource_type,
            request.url,
            request.failure,
        )

    def _on_web_error(self, web_error: Any) -> None:
        # weberror приходит как отдельный объект, внутри которого лежит реальная browser Error.
        error = getattr(web_error, "error", web_error)
        page = getattr(web_error, "page", None)
        page_url = page.url if page is not None else "<unknown-page>"
        message = getattr(error, "message", str(error))
        self.loggers.errors.error("weberror page=%s message=%s", page_url, message)
        self.loggers.app.warning("Browser context emitted weberror. page=%s message=%s", page_url, message)
