from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote

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
from pyvirtualdisplay.display import Display

from config import AppConfig
from logger_setup import LoggerSet, setup_loggers
from utils import (
    build_artifact_path,
    create_run_directory,
    current_timestamp,
    find_text_occurrences,
    write_json_file,
    write_text_file,
)


class BrowserSessionManager:
    """Single live browser session controlled through an HTTP API."""

    def __init__(self, config: AppConfig, bootstrap_logger: logging.Logger) -> None:
        self.config = config
        self.bootstrap_logger = bootstrap_logger
        self.loggers: LoggerSet | None = None
        self.display: Display | None = None
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.run_timestamp: str | None = None
        self.run_directory: Path | None = None
        self.trace_started = False
        self.operation_counter = 0
        self.session_started_at: float | None = None
        self.last_error: str | None = None
        self.last_action: str | None = None
        self.last_artifacts: dict[str, dict[str, str | None]] = {}

    def start_session(self, target_url: str | None = None, restart_if_running: bool = False) -> dict[str, Any]:
        if self.is_session_active():
            if not restart_if_running:
                self._log_info("Session already active. Returning current state without restart.")
                return self.get_state()
            self._log_info("Restarting existing session by request.")
            self.close_session()

        target_url = (target_url or self.config.target_url).strip()
        if not target_url:
            raise ValueError("target_url is required to start a session.")

        self.run_timestamp = current_timestamp()
        self.run_directory = create_run_directory(self.config.output_dir, self.run_timestamp)
        self.loggers = setup_loggers(
            run_directory=self.run_directory,
            run_timestamp=self.run_timestamp,
            log_level=self.config.log_level,
            save_console_log=self.config.save_console_log,
            save_network_log=self.config.save_network_log,
            save_error_log=self.config.save_error_log,
        )
        self.operation_counter = 0
        self.session_started_at = perf_counter()
        self.last_error = None
        self.last_action = "session_start"
        self.last_artifacts = {}

        self._log_info("Live browser session startup initiated.")
        self._log_info(
            "Loaded configuration: %s",
            json.dumps(self.config.to_log_dict(), ensure_ascii=False, sort_keys=True),
        )
        self._log_info("Artifacts directory: %s", self.run_directory)

        if not self.config.headless:
            self._log_info(
                "HEADLESS=false detected. Starting virtual display with size %sx%s.",
                self.config.viewport_width,
                self.config.viewport_height,
            )
            self.display = Display(
                visible=False,
                size=(self.config.viewport_width, self.config.viewport_height),
                use_xauth=False,
            )
            self.display.start()
            self._log_info("Virtual display started. display=%s", self.display.new_display_var)

        self._log_info("Starting Playwright runtime.")
        self.playwright = sync_playwright().start()
        self.config.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self._log_info(
            "Launching Chromium persistent context. headless=%s profile_dir=%s",
            self.config.headless,
            self.config.browser_profile_dir,
        )

        context_options: dict[str, Any] = {
            "viewport": {
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            }
        }
        if self.config.user_agent:
            context_options["user_agent"] = self.config.user_agent

        self._log_info("Creating persistent browser context with options: %s", context_options)
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.browser_profile_dir),
            headless=self.config.headless,
            args=["--disable-dev-shm-usage"],
            **context_options,
        )
        self.browser = self.context.browser
        if self.browser is None:
            raise RuntimeError("Persistent browser context did not expose a browser instance.")
        self._log_info("Chromium persistent context started successfully.")
        self.context.set_default_timeout(self.config.browser_timeout_ms)
        self.context.set_default_navigation_timeout(self.config.browser_timeout_ms)
        self._log_info("Browser context created.")

        self._log_info("Starting Playwright trace recording.")
        self.context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
            title=f"browser_api_session_{self.run_timestamp}",
        )
        self.trace_started = True
        self.page = self.context.new_page()
        self._log_info("Page instance created.")
        self._subscribe_page_events()
        self.save_snapshot("browser_started", save_html=False, save_text=False)
        self.navigate(
            url=target_url,
            wait_for_networkidle=True,
            extra_wait_ms=self.config.extra_wait_ms,
        )
        return self.get_state()

    def close_session(self) -> dict[str, Any]:
        if not self.is_session_active():
            return self.get_state()

        self.last_action = "session_close"
        self._log_info("Closing live browser session.")
        try:
            self.save_snapshot("final", save_html=self.config.save_html, save_text=self.config.save_text)
        except Exception:
            self._log_exception("Failed to save final snapshot.")

        self._save_storage_state()
        self._save_trace()

        if self.page is not None:
            try:
                self.page.close()
            except Exception:
                self._log_exception("Failed to close page cleanly.")
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                self._log_exception("Failed to close browser context cleanly.")
        elif self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                self._log_exception("Failed to close browser cleanly.")
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                self._log_exception("Failed to stop Playwright cleanly.")
        if self.display is not None:
            try:
                self.display.stop()
            except Exception:
                self._log_exception("Failed to stop virtual display cleanly.")

        self.display = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.trace_started = False
        self.session_started_at = None
        state = self.get_state()
        self.loggers = None
        return state

    def navigate(self, url: str, wait_for_networkidle: bool = True, extra_wait_ms: int | None = None) -> dict[str, Any]:
        page = self._require_page()
        self.last_action = "navigate"
        self._log_info("Navigating to URL: %s", url)
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.config.browser_timeout_ms,
        )
        if response is None:
            self._log_info("Navigation completed without response object. current_url=%s", page.url)
        else:
            self._log_info("Navigation completed. current_url=%s status=%s", page.url, response.status)
        self.wait_for_page_ready(
            wait_for_networkidle=wait_for_networkidle,
            extra_wait_ms=self.config.extra_wait_ms if extra_wait_ms is None else extra_wait_ms,
            timeout_ms=self.config.browser_timeout_ms,
            snapshot_name="after_navigate",
        )
        return self.get_state()

    def wait_for_page_ready(
        self,
        wait_for_networkidle: bool = True,
        extra_wait_ms: int = 0,
        timeout_ms: int | None = None,
        snapshot_name: str = "page_ready",
    ) -> dict[str, Any]:
        page = self._require_page()
        timeout_ms = timeout_ms or self.config.browser_timeout_ms
        self.last_action = "wait_for_page_ready"
        self._log_info(
            "Waiting for page readiness. wait_for_networkidle=%s extra_wait_ms=%s timeout_ms=%s",
            wait_for_networkidle,
            extra_wait_ms,
            timeout_ms,
        )
        if wait_for_networkidle:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            self._log_info("networkidle reached.")
        if extra_wait_ms > 0:
            page.wait_for_timeout(extra_wait_ms)
            self._log_info("Extra wait finished. extra_wait_ms=%s", extra_wait_ms)
        self.save_snapshot(snapshot_name, save_html=self.config.save_html, save_text=False)
        return self.get_state()

    def fill_selector(self, selector: str, value: str, action_name: str = "fill selector") -> dict[str, Any]:
        locator = self._wait_for_selector(selector, action_name, self.config.browser_timeout_ms)
        self.last_action = "fill_selector"
        self._log_info(
            "Filling selector. action=%s selector=%s value_length=%s",
            action_name,
            selector,
            len(value),
        )
        locator.fill("")
        locator.fill(value, timeout=self.config.browser_timeout_ms)
        self.save_snapshot(self._sanitize_name(action_name), save_html=False, save_text=False)
        return self.get_state()

    def click_selector(
        self,
        selector: str,
        action_name: str = "click selector",
        wait_for_networkidle: bool = False,
        extra_wait_ms: int = 0,
    ) -> dict[str, Any]:
        locator = self._wait_for_selector(selector, action_name, self.config.browser_timeout_ms)
        self.last_action = "click_selector"
        self._log_info("Clicking selector. action=%s selector=%s", action_name, selector)
        locator.click(timeout=self.config.browser_timeout_ms)
        if wait_for_networkidle or extra_wait_ms > 0:
            self.wait_for_page_ready(
                wait_for_networkidle=wait_for_networkidle,
                extra_wait_ms=extra_wait_ms,
                timeout_ms=self.config.browser_timeout_ms,
                snapshot_name=f"{self._sanitize_name(action_name)}_post_click",
            )
        else:
            self.save_snapshot(f"{self._sanitize_name(action_name)}_clicked", save_html=False, save_text=False)
        return self.get_state()

    def press_selector(self, selector: str, key: str, action_name: str = "press key") -> dict[str, Any]:
        locator = self._wait_for_selector(selector, action_name, self.config.browser_timeout_ms)
        self.last_action = "press_selector"
        self._log_info("Pressing key. action=%s selector=%s key=%s", action_name, selector, key)
        locator.press(key, timeout=self.config.browser_timeout_ms)
        self.save_snapshot(f"{self._sanitize_name(action_name)}_pressed", save_html=False, save_text=False)
        return self.get_state()

    def submit_auth_phone(
        self,
        phone: str | None = None,
        selector: str | None = None,
        submit_selector: str | None = None,
        wait_ms: int | None = None,
    ) -> dict[str, Any]:
        phone = (phone or self.config.auth_phone or "").strip()
        selector = selector or self.config.auth_phone_selector
        submit_selector = submit_selector or self.config.auth_phone_submit_selector
        if not phone:
            raise ValueError("Phone value is required.")
        if not selector:
            raise ValueError("Phone selector is required.")
        self.fill_selector(selector, phone, action_name="auth phone input")
        if submit_selector:
            self.click_selector(submit_selector, action_name="auth phone submit")
        else:
            self.press_selector(selector, "Enter", action_name="auth phone submit enter")
        wait_ms = self.config.auth_wait_after_phone_submit_ms if wait_ms is None else wait_ms
        if wait_ms > 0:
            self.wait_for_page_ready(
                wait_for_networkidle=False,
                extra_wait_ms=wait_ms,
                timeout_ms=self.config.browser_timeout_ms,
                snapshot_name="after_auth_phone_submit_wait",
            )
        return self.get_state()

    def submit_auth_code(
        self,
        code: str,
        selector: str | None = None,
        submit_selector: str | None = None,
        wait_for_networkidle: bool = True,
        extra_wait_ms: int | None = None,
    ) -> dict[str, Any]:
        selector = selector or self.config.auth_code_selector
        submit_selector = submit_selector or self.config.auth_code_submit_selector
        if not code.strip():
            raise ValueError("Code value is required.")
        if not selector:
            raise ValueError("Code selector is required.")
        self.fill_selector(selector, code.strip(), action_name="auth code input")
        if submit_selector:
            self.click_selector(submit_selector, action_name="auth code submit")
        else:
            self.press_selector(selector, "Enter", action_name="auth code submit enter")
        self.wait_for_page_ready(
            wait_for_networkidle=wait_for_networkidle,
            extra_wait_ms=self.config.auth_wait_after_code_submit_ms if extra_wait_ms is None else extra_wait_ms,
            timeout_ms=self.config.browser_timeout_ms,
            snapshot_name="after_auth_code_submit",
        )
        return self.get_state()

    def find_button_by_div_text(
        self,
        div_text: str,
        click: bool = False,
        timeout_ms: int | None = None,
        post_click_wait_ms: int | None = None,
    ) -> dict[str, Any]:
        page = self._require_page()
        timeout_ms = timeout_ms or self.config.target_button_timeout_ms
        post_click_wait_ms = self.config.target_button_click_wait_ms if post_click_wait_ms is None else post_click_wait_ms
        self.last_action = "find_button_by_div_text"
        text_pattern = self._build_whitespace_tolerant_regex(div_text)
        locator = page.locator("button", has=page.locator("div", has_text=text_pattern)).first
        self._log_info(
            "Waiting for target button. div_text=%r timeout_ms=%s current_url=%s",
            div_text,
            timeout_ms,
            page.url,
        )
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            matching_div_count = page.locator("div", has_text=text_pattern).count()
            message = (
                "Target button not found within timeout. "
                f"div_text={div_text!r} current_url={page.url} matching_div_count={matching_div_count}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("target_button_not_found", save_html=True, save_text=True)
            raise TimeoutError(message) from exc

        self._log_info("Target button found. div_text=%r", div_text)
        self.save_snapshot("target_button_found", save_html=False, save_text=False)
        if click:
            locator.click(timeout=timeout_ms)
            self._log_info("Target button clicked. div_text=%r", div_text)
            self.wait_for_page_ready(
                wait_for_networkidle=False,
                extra_wait_ms=post_click_wait_ms,
                timeout_ms=self.config.browser_timeout_ms,
                snapshot_name="after_target_button_click",
            )
        state = self.get_state()
        state["target_button_text"] = div_text
        state["target_button_clicked"] = click
        return state

    def hover_profile_menu_and_click_item(
        self,
        profile_selector: str,
        target_menu_text: str,
        current_button_text: str | None = None,
        timeout_ms: int | None = None,
        hover_wait_ms: int = 500,
        post_click_wait_ms: int = 3000,
    ) -> dict[str, Any]:
        page = self._require_page()
        timeout_ms = timeout_ms or self.config.target_button_timeout_ms
        self.last_action = "hover_profile_menu_and_click_item"

        profile = self._wait_for_selector(profile_selector, "profile container", timeout_ms)
        self._log_info(
            "Profile container located. selector=%s current_url=%s",
            profile_selector,
            page.url,
        )

        if current_button_text:
            current_pattern = self._build_whitespace_tolerant_regex(current_button_text)
            trigger_button = profile.locator(
                "button",
                has=page.locator("div", has_text=current_pattern),
            ).first
            self._log_info(
                "Looking for current profile button by text. current_button_text=%r",
                current_button_text,
            )
        else:
            trigger_button = profile.locator("button").first
            self._log_info("current_button_text is not provided. Using first button inside ProfileView.")

        try:
            trigger_button.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            message = (
                "Profile trigger button was not found. "
                f"profile_selector={profile_selector!r} current_button_text={current_button_text!r} current_url={page.url}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("profile_trigger_not_found", save_html=True, save_text=True)
            raise TimeoutError(message) from exc

        self._log_info("Hovering trigger button to open profile menu.")
        trigger_button.hover(timeout=timeout_ms)
        if hover_wait_ms > 0:
            page.wait_for_timeout(hover_wait_ms)
        self.save_snapshot("profile_menu_after_hover", save_html=False, save_text=False)

        target_pattern = self._build_whitespace_tolerant_regex(target_menu_text)
        menu_item = trigger_button.locator(
            "xpath=following-sibling::*[1]//li",
            has=page.locator("*", has_text=target_pattern),
        ).first

        try:
            menu_item.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            matching_li_count = trigger_button.locator(
                "xpath=following-sibling::*[1]//li",
                has=page.locator("*", has_text=target_pattern),
            ).count()
            message = (
                "Profile menu item was not found after hover. "
                f"target_menu_text={target_menu_text!r} current_button_text={current_button_text!r} "
                f"matching_li_count={matching_li_count} current_url={page.url}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("profile_menu_item_not_found", save_html=True, save_text=True)
            raise TimeoutError(message) from exc

        self._log_info(
            "Profile menu item found. target_menu_text=%r current_button_text=%r",
            target_menu_text,
            current_button_text,
        )
        self.save_snapshot("profile_menu_item_found", save_html=False, save_text=False)
        menu_item.click(timeout=timeout_ms)
        self._log_info("Profile menu item clicked. target_menu_text=%r", target_menu_text)

        if post_click_wait_ms > 0:
            self.wait_for_page_ready(
                wait_for_networkidle=False,
                extra_wait_ms=post_click_wait_ms,
                timeout_ms=self.config.browser_timeout_ms,
                snapshot_name="after_profile_menu_item_click",
            )
        else:
            self.save_snapshot("after_profile_menu_item_click", save_html=False, save_text=False)

        state = self.get_state()
        state["profile_selector"] = profile_selector
        state["current_button_text"] = current_button_text
        state["target_menu_text"] = target_menu_text
        return state

    def click_anchor_by_span_text(
        self,
        text: str,
        scope_selector: str | None = None,
        timeout_ms: int | None = None,
        post_click_wait_ms: int = 3000,
    ) -> dict[str, Any]:
        page = self._require_page()
        timeout_ms = timeout_ms or self.config.target_button_timeout_ms
        self.last_action = "click_anchor_by_span_text"
        text_pattern = self._build_whitespace_tolerant_regex(text)

        scope = page.locator(scope_selector) if scope_selector else page.locator("body")
        anchor = scope.locator(
            "li",
            has=page.locator("a", has=page.locator("div span", has_text=text_pattern)),
        ).locator("a", has=page.locator("div span", has_text=text_pattern)).first

        self._log_info(
            "Looking for anchor by nested span text. text=%r scope_selector=%r timeout_ms=%s current_url=%s",
            text,
            scope_selector,
            timeout_ms,
            page.url,
        )

        try:
            anchor.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            matching_anchor_count = scope.locator(
                "li",
                has=page.locator("a", has=page.locator("div span", has_text=text_pattern)),
            ).count()
            message = (
                "Anchor by span text was not found. "
                f"text={text!r} scope_selector={scope_selector!r} matching_anchor_count={matching_anchor_count} current_url={page.url}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("anchor_by_span_not_found", save_html=True, save_text=True)
            raise TimeoutError(message) from exc

        self._log_info("Anchor found by span text. text=%r", text)
        self.save_snapshot("anchor_by_span_found", save_html=False, save_text=False)
        anchor.click(timeout=timeout_ms)
        self._log_info("Anchor clicked by span text. text=%r", text)

        if post_click_wait_ms > 0:
            self.wait_for_page_ready(
                wait_for_networkidle=False,
                extra_wait_ms=post_click_wait_ms,
                timeout_ms=self.config.browser_timeout_ms,
                snapshot_name="after_anchor_by_span_click",
            )
        else:
            self.save_snapshot("after_anchor_by_span_click", save_html=False, save_text=False)

        state = self.get_state()
        state["clicked_span_text"] = text
        state["scope_selector"] = scope_selector
        return state

    def search_text(self, text: str) -> dict[str, Any]:
        page_text = self.get_visible_text()
        found, matches_count, positions = find_text_occurrences(page_text, text)
        result = {
            "search_text": text,
            "found": found,
            "matches_count": matches_count,
            "positions": positions[:50],
            "current_url": self.page.url if self.page else None,
            "run_timestamp": self.run_timestamp,
        }
        artifact_path = self._build_step_path("search_result", "json")
        write_json_file(artifact_path, result)
        self._log_info("Search result JSON saved: %s", artifact_path)
        snapshot_artifacts = self.save_snapshot("after_search", save_html=False, save_text=True)
        result["artifacts"] = {
            "search_result": self._artifact_ref(artifact_path),
            **snapshot_artifacts,
        }
        self.last_artifacts = result["artifacts"]
        return result

    def extract_size_by_code(
        self,
        code: str,
        row_selector: str = "[data-testid='Table-row-view']",
        value_cell_selector: str = "[data-testid='Cell--title']",
        size_regex: str = r"Р-р\s+([^\n\r]+)",
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        page = self._require_page()
        timeout_ms = timeout_ms or self.config.browser_timeout_ms
        self.last_action = "extract_size_by_code"

        code = code.strip()
        if not code:
            raise ValueError("code must not be empty.")

        row_candidates = page.locator(row_selector)
        row_by_input = row_candidates.filter(
            has=page.locator(f"input[id='{code}'], input[name='{code}']"),
        ).first
        row_by_text = row_candidates.filter(has_text=self._build_whitespace_tolerant_regex(code)).first

        self._log_info(
            "Looking for row by code. code=%r row_selector=%r value_cell_selector=%r timeout_ms=%s current_url=%s",
            code,
            row_selector,
            value_cell_selector,
            timeout_ms,
            page.url,
        )

        row = row_by_input
        try:
            row.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            row = row_by_text
            try:
                row.wait_for(state="visible", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                matching_row_count_by_input = row_candidates.filter(
                    has=page.locator(f"input[id='{code}'], input[name='{code}']"),
                ).count()
                matching_row_count_by_text = row_candidates.filter(
                    has_text=self._build_whitespace_tolerant_regex(code),
                ).count()
                message = (
                    "Row by code was not found. "
                    f"code={code!r} row_selector={row_selector!r} "
                    f"matching_row_count_by_input={matching_row_count_by_input} "
                    f"matching_row_count_by_text={matching_row_count_by_text} current_url={page.url}"
                )
                self.last_error = message
                self._log_exception(message)
                self.save_snapshot("extract_size_row_not_found", save_html=True, save_text=True)
                raise TimeoutError(message) from exc

        value_cell = row.locator(value_cell_selector).first
        try:
            value_cell.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            message = (
                "Value cell was not found in row by code. "
                f"code={code!r} value_cell_selector={value_cell_selector!r} current_url={page.url}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("extract_size_value_cell_not_found", save_html=True, save_text=True)
            raise TimeoutError(message) from exc

        cell_text = value_cell.inner_text(timeout=timeout_ms).strip()
        row_text = row.inner_text(timeout=timeout_ms).strip()

        try:
            compiled_size_regex = re.compile(size_regex, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid size_regex: {size_regex!r}") from exc

        match = compiled_size_regex.search(cell_text)
        if match is None:
            match = compiled_size_regex.search(row_text)

        if match is None:
            message = (
                "Size pattern was not found in row text. "
                f"code={code!r} size_regex={size_regex!r} current_url={page.url}"
            )
            self.last_error = message
            self._log_exception(message)
            self.save_snapshot("extract_size_pattern_not_found", save_html=True, save_text=True)
            raise ValueError(message)

        extracted_size = next((group.strip() for group in match.groups() if group and group.strip()), match.group(0).strip())

        self.save_snapshot("extract_size_by_code_found", save_html=False, save_text=False)
        result = self.get_state()
        result["requested_code"] = code
        result["size"] = extracted_size
        result["matched_text"] = match.group(0).strip()
        result["value_cell_text"] = cell_text
        return result

    def save_snapshot(
        self,
        name: str,
        save_html: bool | None = None,
        save_text: bool | None = None,
    ) -> dict[str, dict[str, str | None]]:
        page = self._require_page()
        save_html = self.config.save_html if save_html is None else save_html
        save_text = self.config.save_text if save_text is None else save_text
        snapshot_name = self._sanitize_name(name)
        results: dict[str, dict[str, str | None]] = {}

        screenshot_path = self._build_step_path(snapshot_name, "png")
        page.screenshot(path=str(screenshot_path), full_page=self.config.screenshot_full_page)
        self._log_info("Screenshot saved: %s", screenshot_path)
        results["screenshot"] = self._artifact_ref(screenshot_path)

        if save_html:
            html_path = self._build_step_path(f"{snapshot_name}_source", "html")
            write_text_file(html_path, page.content())
            self._log_info("HTML saved: %s", html_path)
            results["html"] = self._artifact_ref(html_path)

        if save_text:
            text_path = self._build_step_path(f"{snapshot_name}_text", "txt")
            write_text_file(text_path, self.get_visible_text())
            self._log_info("Text dump saved: %s", text_path)
            results["text"] = self._artifact_ref(text_path)

        self.last_artifacts = results
        return results

    def get_visible_text(self) -> str:
        page = self._require_page()
        try:
            page_text = page.locator("body").inner_text(timeout=self.config.browser_timeout_ms)
        except Exception:
            self._log_exception("Failed to extract body.inner_text(). Falling back to document.body.innerText.")
            page_text = page.evaluate("document.body ? document.body.innerText : ''")
        return page_text.strip()

    def get_state(self) -> dict[str, Any]:
        current_url: str | None = None
        page_title: str | None = None
        if self.page is not None:
            try:
                current_url = self.page.url
                page_title = self.page.title()
            except Exception:
                page_title = None

        uptime_ms = None
        if self.session_started_at is not None:
            uptime_ms = int((perf_counter() - self.session_started_at) * 1000)

        return {
            "session_active": self.is_session_active(),
            "current_url": current_url,
            "page_title": page_title,
            "run_timestamp": self.run_timestamp,
            "run_directory": str(self.run_directory) if self.run_directory else None,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "last_artifacts": self.last_artifacts,
            "session_uptime_ms": uptime_ms,
        }

    def is_session_active(self) -> bool:
        return self.page is not None and self.context is not None and self.browser is not None

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("No active browser session. Call /session/start first.")
        return self.page

    def _wait_for_selector(self, selector: str, action_name: str, timeout_ms: int):
        page = self._require_page()
        self._log_info("Waiting for selector. action=%s selector=%s timeout_ms=%s", action_name, selector, timeout_ms)
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        self._log_info("Selector ready. action=%s selector=%s", action_name, selector)
        return locator

    def _subscribe_page_events(self) -> None:
        if self.page is None or self.context is None:
            raise RuntimeError("Cannot subscribe to page events before session initialization.")
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

    def _build_step_path(self, name: str, extension: str) -> Path:
        if self.run_directory is None:
            raise RuntimeError("Run directory is not initialized.")
        self.operation_counter += 1
        return build_artifact_path(
            self.run_directory,
            f"{self.operation_counter:03d}",
            self._sanitize_name(name),
            extension,
        )

    def _artifact_ref(self, path: Path) -> dict[str, str | None]:
        return {
            "path": str(path),
            "url": self._build_public_artifact_url(path),
        }

    def _build_public_artifact_url(self, path: Path) -> str | None:
        if self.config.artifact_public_base_url is None:
            return None
        try:
            relative_path = path.relative_to(self.config.output_dir)
        except ValueError:
            return None

        encoded_segments = [quote(part) for part in relative_path.parts]
        return f"{self.config.artifact_public_base_url}/{'/'.join(encoded_segments)}"

    def _save_storage_state(self) -> None:
        if self.context is None:
            return
        artifact_path = self._build_step_path("storage_state", "json")
        self._log_info("Saving storage state to %s", artifact_path)
        try:
            self.context.storage_state(path=str(artifact_path))
        except Exception:
            self._log_exception("Failed to save storage state.")

    def _save_trace(self) -> None:
        if self.context is None or not self.trace_started:
            return
        artifact_path = self._build_step_path("playwright_trace", "zip")
        self._log_info("Saving Playwright trace to %s", artifact_path)
        try:
            self.context.tracing.stop(path=str(artifact_path))
            self.trace_started = False
        except Exception:
            self._log_exception("Failed to save Playwright trace.")

    def _build_whitespace_tolerant_regex(self, text: str) -> re.Pattern[str]:
        tokens = text.split()
        if not tokens:
            raise ValueError("div_text must not be empty.")
        pattern = r"\s+".join(re.escape(token) for token in tokens)
        return re.compile(pattern, re.IGNORECASE)

    def _sanitize_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_") or "step"

    def _log_info(self, message: str, *args: Any) -> None:
        logger = self.loggers.app if self.loggers is not None else self.bootstrap_logger
        logger.info(message, *args)

    def _log_exception(self, message: str, *args: Any) -> None:
        logger = self.loggers.app if self.loggers is not None else self.bootstrap_logger
        logger.exception(message, *args)

    def _on_console_message(self, message: ConsoleMessage) -> None:
        if self.loggers is None:
            return
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
        if self.loggers is None:
            return
        self.loggers.errors.error("pageerror message=%s", error.message)
        self.loggers.app.warning("Page emitted pageerror. message=%s", error.message)

    def _on_dialog(self, dialog: Dialog) -> None:
        if self.loggers is None:
            return
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
        self._log_info("Page event: domcontentloaded. url=%s", self.page.url if self.page else "<no-page>")

    def _on_page_load(self) -> None:
        self._log_info("Page event: load. url=%s", self.page.url if self.page else "<no-page>")

    def _on_frame_navigated(self, frame: Any) -> None:
        self._log_info(
            "Frame navigated. name=%s url=%s is_main=%s",
            frame.name,
            frame.url,
            frame == (self.page.main_frame if self.page is not None else None),
        )

    def _on_request(self, request: Request) -> None:
        if self.loggers is None:
            return
        redirected_from = request.redirected_from.url if request.redirected_from else ""
        self.loggers.network.info(
            "request method=%s resource_type=%s is_navigation=%s url=%s redirected_from=%s",
            request.method,
            request.resource_type,
            request.is_navigation_request(),
            request.url,
            redirected_from,
        )

    def _on_response(self, response: Response) -> None:
        if self.loggers is None:
            return
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
        if self.loggers is None:
            return
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
        if self.loggers is None:
            return
        error = getattr(web_error, "error", web_error)
        page = getattr(web_error, "page", None)
        page_url = page.url if page is not None else "<unknown-page>"
        message = getattr(error, "message", str(error))
        self.loggers.errors.error("weberror page=%s message=%s", page_url, message)
        self.loggers.app.warning("Browser context emitted weberror. page=%s message=%s", page_url, message)
