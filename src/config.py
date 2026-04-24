from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


def _parse_bool(name: str, default: bool) -> bool:
    # Разрешаем несколько форматов значений, чтобы .env было удобнее править руками.
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Environment variable {name} must be a boolean-like value, got: {raw_value!r}")


def _parse_int(name: str, default: int) -> int:
    # Пустое значение трактуем как "использовать дефолт", а не как ошибку.
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got: {raw_value!r}") from exc


def _mask_value(value: str | None, visible_suffix: int = 4) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if visible_suffix <= 0:
        return "*" * len(value)
    if len(value) <= visible_suffix:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible_suffix)}{value[-visible_suffix:]}"


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    parts = urlsplit(cleaned)
    normalized_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, normalized_path, parts.query, parts.fragment))


@dataclass(slots=True)
class AppConfig:
    target_url: str
    search_text: str
    headless: bool
    browser_timeout_ms: int
    extra_wait_ms: int
    output_dir: Path
    log_level: str
    screenshot_full_page: bool
    save_html: bool
    save_text: bool
    save_network_log: bool
    save_console_log: bool
    save_error_log: bool
    user_agent: str | None
    viewport_width: int
    viewport_height: int
    api_host: str
    api_port: int
    artifact_public_base_url: str | None
    auth_enabled: bool
    auth_phone: str | None
    auth_phone_selector: str | None
    auth_phone_submit_selector: str | None
    auth_code: str | None
    auth_code_file: Path | None
    auth_code_selector: str | None
    auth_code_submit_selector: str | None
    auth_code_timeout_ms: int
    auth_code_poll_interval_ms: int
    auth_wait_after_phone_submit_ms: int
    auth_wait_after_code_submit_ms: int
    auth_interactive_code_entry: bool
    post_auth_wait_for_networkidle: bool
    post_auth_extra_wait_ms: int
    target_button_div_text: str | None
    target_button_timeout_ms: int
    click_target_button: bool
    target_button_click_wait_ms: int
    keep_session_alive: bool
    keep_alive_log_interval_ms: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        # load_dotenv() оставляет Docker env приоритетными, но позволяет запускать код и локально.
        load_dotenv(override=False)

        target_url = os.getenv("TARGET_URL", "").strip()
        if not target_url:
            raise ValueError("TARGET_URL is required.")

        user_agent = os.getenv("USER_AGENT", "").strip() or None
        auth_phone = os.getenv("AUTH_PHONE", "").strip() or None
        auth_phone_selector = os.getenv("AUTH_PHONE_SELECTOR", "").strip() or None
        auth_phone_submit_selector = os.getenv("AUTH_PHONE_SUBMIT_SELECTOR", "").strip() or None
        auth_code = os.getenv("AUTH_CODE", "").strip() or None
        auth_code_file_raw = os.getenv("AUTH_CODE_FILE", "").strip()
        auth_code_selector = os.getenv("AUTH_CODE_SELECTOR", "").strip() or None
        auth_code_submit_selector = os.getenv("AUTH_CODE_SUBMIT_SELECTOR", "").strip() or None
        target_button_div_text = os.getenv("TARGET_BUTTON_DIV_TEXT", "").strip() or None
        config = cls(
            target_url=target_url,
            search_text=os.getenv("SEARCH_TEXT", "").strip(),
            headless=_parse_bool("HEADLESS", True),
            browser_timeout_ms=_parse_int("BROWSER_TIMEOUT_MS", 60000),
            extra_wait_ms=_parse_int("EXTRA_WAIT_MS", 3000),
            output_dir=Path(os.getenv("OUTPUT_DIR", "/app/output")).expanduser(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            screenshot_full_page=_parse_bool("SCREENSHOT_FULL_PAGE", True),
            save_html=_parse_bool("SAVE_HTML", True),
            save_text=_parse_bool("SAVE_TEXT", True),
            save_network_log=_parse_bool("SAVE_NETWORK_LOG", True),
            save_console_log=_parse_bool("SAVE_CONSOLE_LOG", True),
            save_error_log=_parse_bool("SAVE_ERROR_LOG", True),
            user_agent=user_agent,
            viewport_width=_parse_int("VIEWPORT_WIDTH", 1440),
            viewport_height=_parse_int("VIEWPORT_HEIGHT", 900),
            api_host=os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0",
            api_port=_parse_int("API_PORT", 8000),
            artifact_public_base_url=_normalize_base_url(os.getenv("ARTIFACT_PUBLIC_BASE_URL")),
            auth_enabled=_parse_bool("AUTH_ENABLED", False),
            auth_phone=auth_phone,
            auth_phone_selector=auth_phone_selector,
            auth_phone_submit_selector=auth_phone_submit_selector,
            auth_code=auth_code,
            auth_code_file=Path(auth_code_file_raw).expanduser() if auth_code_file_raw else None,
            auth_code_selector=auth_code_selector,
            auth_code_submit_selector=auth_code_submit_selector,
            auth_code_timeout_ms=_parse_int("AUTH_CODE_TIMEOUT_MS", 180000),
            auth_code_poll_interval_ms=_parse_int("AUTH_CODE_POLL_INTERVAL_MS", 1000),
            auth_wait_after_phone_submit_ms=_parse_int("AUTH_WAIT_AFTER_PHONE_SUBMIT_MS", 3000),
            auth_wait_after_code_submit_ms=_parse_int("AUTH_WAIT_AFTER_CODE_SUBMIT_MS", 5000),
            auth_interactive_code_entry=_parse_bool("AUTH_INTERACTIVE_CODE_ENTRY", True),
            post_auth_wait_for_networkidle=_parse_bool("POST_AUTH_WAIT_FOR_NETWORKIDLE", True),
            post_auth_extra_wait_ms=_parse_int("POST_AUTH_EXTRA_WAIT_MS", 5000),
            target_button_div_text=target_button_div_text,
            target_button_timeout_ms=_parse_int("TARGET_BUTTON_TIMEOUT_MS", 60000),
            click_target_button=_parse_bool("CLICK_TARGET_BUTTON", False),
            target_button_click_wait_ms=_parse_int("TARGET_BUTTON_CLICK_WAIT_MS", 3000),
            keep_session_alive=_parse_bool("KEEP_SESSION_ALIVE", True),
            keep_alive_log_interval_ms=_parse_int("KEEP_ALIVE_LOG_INTERVAL_MS", 30000),
        )
        config.validate()
        return config

    def validate(self) -> None:
        # Явная валидация даёт понятную ошибку до старта браузера и сетевой активности.
        if self.browser_timeout_ms <= 0:
            raise ValueError("BROWSER_TIMEOUT_MS must be greater than 0.")
        if self.extra_wait_ms < 0:
            raise ValueError("EXTRA_WAIT_MS must be 0 or greater.")
        if self.viewport_width <= 0 or self.viewport_height <= 0:
            raise ValueError("VIEWPORT_WIDTH and VIEWPORT_HEIGHT must be greater than 0.")
        if self.api_port <= 0:
            raise ValueError("API_PORT must be greater than 0.")
        if self.auth_code_timeout_ms <= 0:
            raise ValueError("AUTH_CODE_TIMEOUT_MS must be greater than 0.")
        if self.auth_code_poll_interval_ms <= 0:
            raise ValueError("AUTH_CODE_POLL_INTERVAL_MS must be greater than 0.")
        if self.auth_wait_after_phone_submit_ms < 0 or self.auth_wait_after_code_submit_ms < 0:
            raise ValueError("AUTH_WAIT_AFTER_*_MS must be 0 or greater.")
        if self.post_auth_extra_wait_ms < 0:
            raise ValueError("POST_AUTH_EXTRA_WAIT_MS must be 0 or greater.")
        if self.target_button_timeout_ms <= 0:
            raise ValueError("TARGET_BUTTON_TIMEOUT_MS must be greater than 0.")
        if self.target_button_click_wait_ms < 0:
            raise ValueError("TARGET_BUTTON_CLICK_WAIT_MS must be 0 or greater.")
        if self.keep_alive_log_interval_ms <= 0:
            raise ValueError("KEEP_ALIVE_LOG_INTERVAL_MS must be greater than 0.")
        if self.auth_enabled:
            if not self.auth_phone:
                raise ValueError("AUTH_PHONE is required when AUTH_ENABLED=true.")
            if not self.auth_phone_selector:
                raise ValueError("AUTH_PHONE_SELECTOR is required when AUTH_ENABLED=true.")
            if not self.auth_code_selector:
                raise ValueError("AUTH_CODE_SELECTOR is required when AUTH_ENABLED=true.")

    def to_log_dict(self) -> dict[str, str | int | bool | None]:
        payload = asdict(self)
        payload["output_dir"] = str(self.output_dir)
        payload["auth_code_file"] = str(self.auth_code_file) if self.auth_code_file else None
        payload["auth_phone"] = _mask_value(self.auth_phone)
        payload["auth_code"] = _mask_value(self.auth_code, visible_suffix=0)
        return payload
