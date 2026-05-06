from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import AppConfig


@dataclass(slots=True)
class AuthScenario:
    phone_selector: str | None
    phone_submit_selector: str | None
    code_selector: str | None
    code_submit_selector: str | None
    phone_wait_ms: int
    code_wait_ms: int


@dataclass(slots=True)
class CabinetScenario:
    profile_selector: str
    current_button_text: str | None
    target_menu_text: str | None
    hover_wait_ms: int
    post_click_wait_ms: int


@dataclass(slots=True)
class WorkspaceScenario:
    url: str
    wait_for_networkidle: bool
    extra_wait_ms: int


@dataclass(slots=True)
class CloseModalScenario:
    selector: str | None
    div_text: str | None
    click_wait_ms: int


@dataclass(slots=True)
class SearchSizeScenario:
    row_selector: str
    value_cell_selector: str
    size_regex: str
    timeout_ms: int


@dataclass(slots=True)
class ScenarioConfig:
    name: str
    auth: AuthScenario
    cabinet: CabinetScenario
    workspace: WorkspaceScenario
    close_modal: CloseModalScenario
    search_size: SearchSizeScenario

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "ScenarioConfig":
        payload: dict[str, Any] = {}
        if config.scenario_config_file and config.scenario_config_file.exists():
            with config.scenario_config_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)

        def section(name: str) -> dict[str, Any]:
            value = payload.get(name, {})
            return value if isinstance(value, dict) else {}

        auth = section("auth")
        cabinet = section("cabinet")
        workspace = section("workspace")
        close_modal = section("close_modal")
        search_size = section("search_size")

        return cls(
            name=str(payload.get("name") or "default"),
            auth=AuthScenario(
                phone_selector=auth.get("phone_selector") or config.auth_phone_selector,
                phone_submit_selector=auth.get("phone_submit_selector") or config.auth_phone_submit_selector,
                code_selector=auth.get("code_selector") or config.auth_code_selector,
                code_submit_selector=auth.get("code_submit_selector") or config.auth_code_submit_selector,
                phone_wait_ms=int(auth.get("phone_wait_ms") or config.auth_wait_after_phone_submit_ms),
                code_wait_ms=int(auth.get("code_wait_ms") or config.auth_wait_after_code_submit_ms),
            ),
            cabinet=CabinetScenario(
                profile_selector=str(cabinet.get("profile_selector") or ".ProfileView"),
                current_button_text=cabinet.get("current_button_text"),
                target_menu_text=cabinet.get("target_menu_text"),
                hover_wait_ms=int(cabinet.get("hover_wait_ms") or 500),
                post_click_wait_ms=int(cabinet.get("post_click_wait_ms") or 3000),
            ),
            workspace=WorkspaceScenario(
                url=str(workspace.get("url") or config.workspace_url or config.target_url),
                wait_for_networkidle=bool(workspace.get("wait_for_networkidle", False)),
                extra_wait_ms=int(workspace.get("extra_wait_ms") or config.extra_wait_ms),
            ),
            close_modal=CloseModalScenario(
                selector=close_modal.get("selector") or config.close_modal_selector,
                div_text=close_modal.get("div_text") or config.close_modal_div_text,
                click_wait_ms=int(close_modal.get("click_wait_ms") or config.close_modal_click_wait_ms),
            ),
            search_size=SearchSizeScenario(
                row_selector=str(search_size.get("row_selector") or config.search_row_selector),
                value_cell_selector=str(search_size.get("value_cell_selector") or config.search_value_cell_selector),
                size_regex=str(search_size.get("size_regex") or config.search_size_regex),
                timeout_ms=int(search_size.get("timeout_ms") or config.browser_timeout_ms),
            ),
        )
