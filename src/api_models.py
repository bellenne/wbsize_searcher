from __future__ import annotations

from pydantic import BaseModel, Field


class SessionStartRequest(BaseModel):
    target_url: str | None = None
    restart_if_running: bool = False


class NavigateRequest(BaseModel):
    url: str
    wait_for_networkidle: bool = False
    extra_wait_ms: int | None = None


class WaitRequest(BaseModel):
    wait_for_networkidle: bool = False
    extra_wait_ms: int = 0
    timeout_ms: int | None = None
    snapshot_name: str = "manual_wait"


class FillRequest(BaseModel):
    selector: str
    value: str
    action_name: str = "fill selector"


class ClickRequest(BaseModel):
    selector: str
    action_name: str = "click selector"
    wait_for_networkidle: bool = False
    extra_wait_ms: int = 0


class PressRequest(BaseModel):
    selector: str
    key: str
    action_name: str = "press key"


class AuthPhoneRequest(BaseModel):
    phone: str | None = None
    selector: str | None = None
    submit_selector: str | None = None
    wait_ms: int | None = None


class AuthCodeRequest(BaseModel):
    code: str
    selector: str | None = None
    submit_selector: str | None = None
    wait_for_networkidle: bool = False
    extra_wait_ms: int | None = None


class FindButtonByDivTextRequest(BaseModel):
    div_text: str
    click: bool = False
    timeout_ms: int | None = None
    post_click_wait_ms: int | None = None


class HoverProfileMenuSelectRequest(BaseModel):
    profile_selector: str = ".ProfileView"
    current_button_text: str | None = None
    target_menu_text: str
    timeout_ms: int | None = None
    hover_wait_ms: int = 500
    post_click_wait_ms: int = 3000


class ClickAnchorBySpanTextRequest(BaseModel):
    text: str
    scope_selector: str | None = None
    timeout_ms: int | None = None
    post_click_wait_ms: int = 3000


class SearchTextRequest(BaseModel):
    text: str


class ExtractSizeByCodeRequest(BaseModel):
    code: str
    row_selector: str = "[data-testid='Table-row-view']"
    value_cell_selector: str = "[data-testid='Cell--title']"
    size_regex: str = r"Р\s*-\s*р[\s\xa0]+([^\n\r]+)"
    timeout_ms: int | None = None


class SnapshotRequest(BaseModel):
    name: str = Field(default="manual_snapshot")
    save_html: bool | None = None
    save_text: bool | None = None


class SearchSizeTaskRequest(BaseModel):
    task_id: str
    external_task_id: str | None = None
    order_number: str | None = None
    code: str | None = None
    force_refresh: bool = False
    close_modal: bool = True
    scenario_name: str | None = None
    cabinet: str | None = None
    account: str | None = None
    attempt: int | None = None
    metadata: dict[str, object] | None = None
