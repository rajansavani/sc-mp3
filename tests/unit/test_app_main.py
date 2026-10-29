"""Tests for opening the window, its ready and closing hooks, the MSHTML fallback, and routing
pywebview's logs."""

import html
import logging
import mimetypes
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scmp3.app import instance, main, page_loading
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.core import log, paths
from scmp3.core.engine_start import EngineStart
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore

IDNO = 7
PAGE = "../web/index.html"
APP_URL = "http://127.0.0.1:5000/index.html"
RETRY_URL = "http://127.0.0.1:5000/index.html?retry"
# What PAGE_CHECK reads on each kind of page: the address, whether api.js ran, and whether the
# page is the UI's own HTML.
UI_PAGE = (APP_URL, "object", True)
ERROR_PAGE = ("chrome-error://chromewebdata/", "undefined", False)
WRONG_ADDRESS = ("http://127.0.0.1:5000/c", "undefined", False)
# An error page from pywebview's server, such as a 404, at the UI's own address.
SERVER_ERROR = (APP_URL, "undefined", False)
# The UI's HTML before its scripts have run, as WKWebView reports it right after the load.
UI_STARTING = (APP_URL, "undefined", True)
LATE_UI_PAGE = [UI_STARTING, UI_STARTING, UI_PAGE]
# The real one, before the fixture below swaps it for running checks in line.
IN_BACKGROUND = page_loading.in_background


@pytest.fixture(autouse=True)
def quick_ui_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UI page whose scripts never start is given up on in a moment, not in 10 s."""
    monkeypatch.setattr(page_loading, "UI_START_SECONDS", 0.2)
    monkeypatch.setattr(page_loading, "UI_POLL_SECONDS", 0.01)


@pytest.fixture(autouse=True)
def checks_in_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Page checks run in the caller's thread, so a test sees their result when open_window ends."""
    monkeypatch.setattr(page_loading, "in_background", lambda task: task())


@pytest.fixture(autouse=True)
def nothing_reaches_the_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that would open the user's browser or a real message box."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        pytest.fail(f"a test reached the desktop with {args}")

    monkeypatch.setattr(webbrowser, "open", refuse)
    monkeypatch.setattr(main, "windows_message_box", refuse)


class FakeEvent:
    """Stands in for a pywebview window event: handlers are added with += and run by set()."""

    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self

    def set(self, *args: Any) -> list[Any]:
        return [handler(*args) for handler in self.handlers]


class FakeWindow:
    """A window whose loads show the given pages in turn: the first load, then each reload.

    A page is what PAGE_CHECK reads there, its address and `typeof window.scmp3`, or an exception
    when the check can't run. A list of those is read one per check, staying on the last.
    """

    def __init__(self, loads: list[Any]) -> None:
        self.events = SimpleNamespace(
            initialized=FakeEvent(), loaded=FakeEvent(), closing=FakeEvent(), closed=FakeEvent()
        )
        self.real_url = APP_URL
        self._loads = list(loads)
        self.page: Any = None
        self.reloads: list[str] = []
        self.html_pages: list[str] = []
        self.scripts: list[str] = []
        self.sent = threading.Event()

    def show_next_page(self) -> None:
        self.page = self._loads.pop(0)
        # pywebview fires `loaded` for every page, error pages included.
        self.events.loaded.set(self)

    def evaluate_js(self, script: str) -> Any:
        if script == page_loading.PAGE_CHECK:
            page = self.page
            if isinstance(page, list):
                page = page.pop(0) if len(page) > 1 else page[0]
            if isinstance(page, Exception):
                raise page
            return list(page)
        self.scripts.append(script)
        self.sent.set()
        return None

    def load_url(self, url: str) -> None:
        self.reloads.append(url)
        # pywebview's load_url makes the new address the window's real_url.
        self.real_url = url
        self.show_next_page()

    def load_html(self, html: str) -> None:
        self.html_pages.append(html)
        self._loads.append(("about:blank", "undefined", False))
        self.show_next_page()


class FakeToolkit:
    """Acts like pywebview: start() fires `initialized` with the renderer, then shows the window."""

    def __init__(
        self, renderer: str = "edgechromium", on_shown: Any = None, loads: Any = None
    ) -> None:
        self.renderer = renderer
        self.settings: dict[str, Any] = {"ALLOW_FILE_URLS": True}
        self.window = FakeWindow(loads or [UI_PAGE])
        self.window_title: str | None = None
        self.window_url: str | None = None
        self.window_options: dict[str, Any] = {}
        self.start_options: dict[str, Any] = {}
        self.shown = False
        self._on_shown = on_shown

    def create_window(self, title: str, url: str | None = None, **options: Any) -> FakeWindow:
        self.window_title, self.window_url, self.window_options = title, url, options
        return self.window

    def start(self, **options: Any) -> None:
        self.start_options = options
        # pywebview cancels the window when an `initialized` handler returns False.
        if False in self.window.events.initialized.set(self.renderer):
            return
        self.shown = True
        self.window.show_next_page()
        if self._on_shown:
            self._on_shown(self.window)
        self.window.events.closed.set()


@pytest.fixture
def api() -> Api:
    context = AppContext(
        settings=SettingsStore(paths.config_dir() / "settings.json"),
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        engine_version=lambda: "1",
    )
    return Api(context)


def test_the_window_has_the_designs_size_and_background(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE)
    assert toolkit.window_title == "sc-mp3"
    assert toolkit.window_options["width"] == 1280
    assert toolkit.window_options["height"] == 860
    assert toolkit.window_options["min_size"] == (980, 640)
    assert toolkit.window_options["background_color"] == "#121110"
    assert toolkit.window_options["js_api"] is api


def test_the_window_loads_the_page_it_is_given(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE)
    assert toolkit.window_url == PAGE


def test_the_window_uses_the_built_in_server_in_private_mode(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE)
    assert toolkit.start_options == {"http_server": True, "private_mode": True}


def test_file_urls_are_turned_off(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE)
    assert toolkit.settings["ALLOW_FILE_URLS"] is False


def test_pywebview_adds_no_inline_style_the_csp_would_block(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE)
    # With text_select off, pywebview injects an inline <style>; base.css does that job instead.
    assert toolkit.window_options["text_select"] is True


def test_a_window_with_webview2_opens_and_exits_cleanly(api: Api) -> None:
    reports: list[str] = []
    toolkit = FakeToolkit("edgechromium")
    code = main.open_window(
        toolkit, api, EventBus(), page=PAGE, report_missing_webview2=lambda: reports.append("x")
    )
    assert code == 0
    assert toolkit.shown
    assert reports == []


def test_mshtml_cancels_the_window_and_offers_webview2(api: Api) -> None:
    reports: list[str] = []
    toolkit = FakeToolkit("mshtml")
    code = main.open_window(
        toolkit, api, EventBus(), page=PAGE, report_missing_webview2=lambda: reports.append("x")
    )
    assert code == 1
    assert not toolkit.shown
    assert reports == ["x"]


def test_a_page_path_with_a_hash_shows_the_folder_problem_instead(api: Api) -> None:
    toolkit = FakeToolkit()
    code = main.open_window(toolkit, api, EventBus(), page=r"D:\C#\sc-mp3\web\index.html")
    assert code == 1
    assert toolkit.window_url is None
    assert "js_api" not in toolkit.window_options
    page = toolkit.window_options["html"]
    assert "a folder that has # in its path" in page
    assert str(main.WEB_DIR) in page


def test_events_reach_the_window_once_it_has_loaded(api: Api) -> None:
    events = EventBus()

    def emit_while_shown(window: FakeWindow) -> None:
        events.emit({"type": "toast", "level": "info", "text": "hello"})
        assert window.sent.wait(5)

    toolkit = FakeToolkit(on_shown=emit_while_shown)
    main.open_window(toolkit, api, events, page=PAGE)
    assert "hello" in toolkit.window.scripts[0]


def emit_hello(events: EventBus) -> Any:
    """An on_shown hook that sends one toast and waits briefly for it to reach the window."""

    def emit_while_shown(window: FakeWindow) -> None:
        events.emit({"type": "toast", "level": "info", "text": "hello"})
        window.sent.wait(0.5)

    return emit_while_shown


@pytest.mark.parametrize(
    "failed_page",
    [ERROR_PAGE, WRONG_ADDRESS, SERVER_ERROR, RuntimeError("no script")],
    ids=["error page", "wrong address", "server error", "check failed"],
)
def test_a_failed_first_load_is_retried_once(api: Api, failed_page: Any) -> None:
    events = EventBus()
    toolkit = FakeToolkit(on_shown=emit_hello(events), loads=[failed_page, UI_PAGE])
    code = main.open_window(toolkit, api, events, page=PAGE)
    assert code == 0
    assert toolkit.window.reloads == [RETRY_URL]
    assert toolkit.window.html_pages == []
    assert "hello" in toolkit.window.scripts[0]


def test_a_second_failed_load_shows_the_plain_page_and_exits_with_1(api: Api) -> None:
    events = EventBus()
    toolkit = FakeToolkit(on_shown=emit_hello(events), loads=[ERROR_PAGE, ERROR_PAGE])
    code = main.open_window(toolkit, api, events, page=PAGE)
    assert code == 1
    assert toolkit.window.reloads == [RETRY_URL]
    [page] = toolkit.window.html_pages
    assert html.escape(page_loading.PAGE_DIDNT_LOAD) in page
    assert html.escape(str(paths.log_dir())) in page
    # The events never started, since the UI never loaded.
    assert toolkit.window.scripts == []


def test_the_retry_goes_to_a_new_address_for_the_same_page() -> None:
    # WebView2 doesn't navigate when told to load the address it's already on.
    assert page_loading.retry_address(APP_URL) == RETRY_URL


def test_a_window_that_loads_the_first_time_isnt_reloaded(api: Api) -> None:
    toolkit = FakeToolkit()
    assert main.open_window(toolkit, api, EventBus(), page=PAGE) == 0
    assert toolkit.window.reloads == []
    assert toolkit.window.html_pages == []


def test_closing_the_window_stops_the_events(api: Api) -> None:
    events = EventBus()
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, events, page=PAGE)
    events.emit({"type": "toast", "level": "info", "text": "too late"})
    assert not toolkit.window.sent.wait(0.3)


@pytest.mark.parametrize(
    ("answer", "opened"), [(main.IDYES, [main.WEBVIEW2_DOWNLOAD_URL]), (IDNO, [])]
)
def test_the_webview2_message_opens_the_download_page_only_on_yes(
    answer: int, opened: list[str]
) -> None:
    shown: list[tuple[str, str, int]] = []
    urls: list[str] = []

    def message_box(text: str, title: str, flags: int) -> int:
        shown.append((text, title, flags))
        return answer

    main.ask_to_install_webview2(message_box=message_box, open_url=urls.append)
    assert urls == opened
    [(text, title, flags)] = shown
    assert main.WEBVIEW2_DOWNLOAD_URL in text
    assert title == "sc-mp3 needs WebView2"
    assert flags == main.MB_YESNO | main.MB_ICONWARNING


def test_the_window_is_handed_over_once_it_exists(api: Api) -> None:
    toolkit = FakeToolkit()
    created: list[Any] = []
    main.open_window(toolkit, api, EventBus(), page=PAGE, on_created=created.append)
    assert created == [toolkit.window]


def test_webview2_keeps_its_data_in_the_folder_it_is_given(api: Api, tmp_path: Path) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE, data_folder=tmp_path / "webview")
    assert toolkit.start_options == {
        "http_server": True,
        "private_mode": True,
        "storage_path": str(tmp_path / "webview"),
    }


def test_pywebviews_early_delete_warning_doesnt_reach_the_log(logging_to_file: Path) -> None:
    main.route_pywebview_logging()
    main.route_pywebview_logging()
    logger = logging.getLogger(main.PYWEBVIEW_LOGGER)
    assert sum(isinstance(item, main.EarlyDeleteFilter) for item in logger.filters) == 1
    logger.warning("Failed to delete user data folder: [WinError 32] in use")
    logger.warning("Something else")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = logging_to_file.read_text(encoding="utf-8")
    assert "Failed to delete user data folder" not in text
    assert "Something else" in text


def test_the_ready_hook_gets_the_window_once_the_ui_has_loaded(api: Api) -> None:
    # The drop handler is bound there, since pywebview binds it to the page's own body.
    toolkit = FakeToolkit()
    ready: list[Any] = []
    main.open_window(toolkit, api, EventBus(), page=PAGE, on_ready=ready.append)
    assert ready == [toolkit.window]


def test_the_log_says_when_the_window_shows_the_ui(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="scmp3.app.page_loading")
    main.open_window(FakeToolkit(loads=[ERROR_PAGE, UI_PAGE]), api, EventBus(), page=PAGE)
    assert [record.message for record in caplog.records].count("The window shows the UI") == 1


def test_a_ui_whose_scripts_start_after_the_load_isnt_reloaded(api: Api) -> None:
    toolkit = FakeToolkit(loads=[list(LATE_UI_PAGE)])
    ready: list[Any] = []
    assert main.open_window(toolkit, api, EventBus(), page=PAGE, on_ready=ready.append) == 0
    assert ready == [toolkit.window]
    assert toolkit.window.reloads == []


@pytest.mark.parametrize("failed_page", [ERROR_PAGE, SERVER_ERROR])
def test_an_error_page_is_retried_without_waiting_for_the_ui_to_start(
    api: Api, monkeypatch: pytest.MonkeyPatch, failed_page: Any
) -> None:
    monkeypatch.setattr(page_loading, "UI_START_SECONDS", 30.0)
    toolkit = FakeToolkit(loads=[failed_page, UI_PAGE])
    started = time.monotonic()
    assert main.open_window(toolkit, api, EventBus(), page=PAGE) == 0
    assert time.monotonic() - started < 5
    assert toolkit.window.reloads == [RETRY_URL]


def test_the_ready_hook_waits_for_the_ui_after_a_failed_first_load(api: Api) -> None:
    toolkit = FakeToolkit(loads=[ERROR_PAGE, UI_PAGE])
    ready: list[Any] = []
    main.open_window(toolkit, api, EventBus(), page=PAGE, on_ready=ready.append)
    assert ready == [toolkit.window]
    assert toolkit.window.reloads == [RETRY_URL]


def test_the_ready_hook_never_runs_when_the_ui_never_loads(api: Api) -> None:
    toolkit = FakeToolkit(loads=[ERROR_PAGE, ERROR_PAGE])
    ready: list[Any] = []
    main.open_window(toolkit, api, EventBus(), page=PAGE, on_ready=ready.append)
    assert ready == []


def test_the_closing_hook_decides_whether_the_window_closes(api: Api) -> None:
    toolkit = FakeToolkit()
    main.open_window(toolkit, api, EventBus(), page=PAGE, on_closing=lambda: False)
    # pywebview cancels the close when a closing handler returns False.
    assert toolkit.window.events.closing.set() == [False]


def test_a_second_copy_exits_without_opening_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    held = instance.instance_lock()
    held.acquire()
    told: list[bool] = []
    monkeypatch.setattr(main, "setup_logging", lambda console: None)
    monkeypatch.setattr(instance, "say_already_open", lambda: told.append(True))
    monkeypatch.setattr(main, "run_window", lambda paths, started: pytest.fail("a window opened"))
    try:
        assert main.run() == 1
    finally:
        held.release()
    assert told == [True]


def test_the_first_copy_lets_go_of_the_lock_when_it_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "setup_logging", lambda console: None)
    monkeypatch.setattr(main, "run_window", lambda paths, started: 0)
    assert main.run() == 0
    after = instance.instance_lock()
    assert instance.another_copy_is_open(after) is False
    after.release()


def test_the_files_the_app_opens_with_reach_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    opened_with: list[Sequence[str]] = []
    engine_starts: list[EngineStart | None] = []

    def run_window(paths: Sequence[str], engine_start: EngineStart | None) -> int:
        opened_with.append(paths)
        engine_starts.append(engine_start)
        return 0

    started = EngineStart(update="2099.01.01")
    monkeypatch.setattr(main, "setup_logging", lambda console: None)
    monkeypatch.setattr(main, "run_window", run_window)
    assert main.run([r"C:\Music\song.mp3"], started) == 0
    assert opened_with == [[r"C:\Music\song.mp3"]]
    assert engine_starts == [started]


@pytest.mark.parametrize(
    "text", [main.WEBVIEW2_MISSING, main.FOLDER_WITH_HASH, page_loading.PAGE_DIDNT_LOAD]
)
def test_the_messages_follow_the_writing_rules(text: str) -> None:
    assert "!" not in text
    assert "\u2014" not in text


@pytest.mark.parametrize("extension", [".js", ".css", ".html", ".woff2"])
def test_web_files_get_the_types_browsers_need(extension: str) -> None:
    mimetypes.add_type("text/plain", extension)
    main.register_web_types()
    assert mimetypes.guess_type(f"file{extension}")[0] == main.WEB_TYPES[extension]


@pytest.fixture
def logging_to_file() -> Any:
    log_file = log.setup_logging()
    yield log_file
    log.close_logging()


def test_pywebview_records_are_masked_like_ours(logging_to_file: Path) -> None:
    import webview  # noqa: F401  # importing it is what adds pywebview's own handler

    pywebview_logger = logging.getLogger(main.PYWEBVIEW_LOGGER)
    # Stands in for the handler pywebview adds, in case another test already removed it.
    pywebview_logger.addHandler(logging.StreamHandler())

    main.route_pywebview_logging()
    pywebview_logger.warning("Cookie: oauth_token=2-123456-789012-AbCdEfGhIjKl")

    assert pywebview_logger.handlers == []
    assert pywebview_logger.propagate
    text = logging_to_file.read_text(encoding="utf-8")
    assert "Cookie: [redacted]" in text
    assert "AbCdEfGhIjKl" not in text


def test_closing_the_window_stops_the_wait_for_the_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_loading, "UI_START_SECONDS", 30.0)
    window = FakeWindow([])
    window.page = UI_STARTING
    closed = threading.Event()
    threading.Timer(0.1, closed.set).start()
    started = time.monotonic()
    assert not page_loading.shows_the_ui(window, closed)
    assert time.monotonic() - started < 5


def test_a_window_closed_before_its_check_isnt_reloaded_or_replaced() -> None:
    watch = page_loading.PageWatch(EventBus())
    window = FakeWindow([ERROR_PAGE])
    window.events.loaded += watch.loaded
    watch.window_closed()
    window.show_next_page()
    assert window.reloads == []
    assert window.html_pages == []
    assert not watch.failed


def test_each_page_check_runs_on_a_daemon_thread() -> None:
    # pywebview's evaluate_js can wait forever on a closed window, which mustn't keep the app open.
    daemons: list[bool] = []
    done = threading.Event()

    def record() -> None:
        daemons.append(threading.current_thread().daemon)
        done.set()

    IN_BACKGROUND(record)
    assert done.wait(5)
    assert daemons == [True]
