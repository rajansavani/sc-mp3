"""Opens the sc-mp3 window, which shows the web UI in web/ through pywebview's built-in server."""

import ctypes
import html
import logging
import mimetypes
import os
import socket
import threading
import time
import webbrowser
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.core import paths
from scmp3.core.log import setup_logging

WINDOW_TITLE = "sc-mp3"
WINDOW_SIZE = (1280, 860)
MIN_WINDOW_SIZE = (980, 640)
# The UI's background, so the window never flashes white while the page loads.
BACKGROUND = "#121110"

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
INDEX_PAGE = WEB_DIR / "index.html"

# The types the UI's files are served with. The Windows registry can map .js to text/plain,
# and browsers refuse to run a module served that way.
WEB_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".map": "application/json",
    ".woff2": "font/woff2",
}

PYWEBVIEW_LOGGER = "pywebview"

# The renderer pywebview falls back to on Windows without WebView2. It can't run the UI.
MSHTML = "mshtml"
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
WEBVIEW2_TITLE = "sc-mp3 needs WebView2"
WEBVIEW2_MISSING = (
    "sc-mp3 needs Microsoft Edge WebView2 Runtime to show its window, and this PC doesn't have"
    f" it.\n\nInstall it from {WEBVIEW2_DOWNLOAD_URL}, then open sc-mp3 again.\n\n"
    "Open the download page now?"
)

# Shown instead of the UI when the page's path has a #, which pywebview's server cuts at.
FOLDER_WITH_HASH = (
    "sc-mp3 can't open from a folder that has # in its path. Move sc-mp3 to a folder without #,"
    " then open it again."
)
# Shown when the UI didn't load twice in a row.
PAGE_DIDNT_LOAD = "sc-mp3's window didn't load. Close it and open sc-mp3 again."
# A page that needs neither the server nor the UI's files, for when those are what failed.
PLAIN_PAGE = """<!doctype html>
<meta charset="utf-8">
<body style="margin:0;padding:32px;background:#121110;color:#F4F1EC;
font:15px/1.45 'Segoe UI',system-ui,sans-serif">
<p>{message}</p>
<p style="color:#ABA59C">{label} <code>{path}</code></p>
</body>"""

# Where the window is, and whether the UI's scripts ran there; api.js defines window.scmp3.
PAGE_CHECK = "[window.location.href, typeof window.scmp3]"
# Added to the page's address for the one retry. The UI ignores it.
RETRY_QUERY = "retry"

# How long to wait for pywebview's server to take connections, and how long each try may take.
# Each try is short, since Windows takes 2 s to refuse a connection to a closed local port.
SERVER_WAIT_SECONDS = 5.0
SERVER_TRY_SECONDS = 0.1
SERVER_PAUSE_SECONDS = 0.05

# Windows MessageBox flags, and the answer for its Yes button.
MB_YESNO = 0x04
MB_ICONWARNING = 0x30
IDYES = 6

MessageBox = Callable[[str, str, int], int]

log = logging.getLogger(__name__)


class Toolkit(Protocol):
    """The parts of the pywebview module the app uses, so tests can pass a fake."""

    settings: MutableMapping[str, Any]

    def create_window(self, title: str, url: str | None = None, **options: Any) -> Any: ...

    def start(self, **options: Any) -> None: ...


class RendererCheck:
    """Handles pywebview's `initialized` event, and cancels the window if the renderer is MSHTML."""

    def __init__(self) -> None:
        self.renderer: str | None = None

    def accept(self, renderer: str) -> bool:
        """Record the renderer. Returning False, as it does for MSHTML, cancels the window."""
        self.renderer = renderer
        return renderer != MSHTML

    @property
    def rejected(self) -> bool:
        return self.renderer == MSHTML


def windows_message_box(text: str, title: str, flags: int) -> int:
    """Show a native Windows message box and return the button pressed."""
    # Native, because the web UI is what can't be shown, and a windowed build has no stderr.
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def ask_to_install_webview2(
    message_box: MessageBox | None = None, open_url: Callable[[str], Any] | None = None
) -> None:
    """Explain that WebView2 is missing, and open its download page if the user says yes."""
    log.warning("pywebview fell back to MSHTML, so the WebView2 Runtime is missing")
    # Looked up here rather than as defaults, so a test that replaces them is never bypassed.
    show = message_box or windows_message_box
    answer = show(WEBVIEW2_MISSING, WEBVIEW2_TITLE, MB_YESNO | MB_ICONWARNING)
    if answer == IDYES:
        (open_url or webbrowser.open)(WEBVIEW2_DOWNLOAD_URL)


def register_web_types() -> None:
    """Give the UI's files the right types in mimetypes, which pywebview's server uses."""
    for extension, content_type in WEB_TYPES.items():
        mimetypes.add_type(content_type, extension)


def route_pywebview_logging() -> None:
    """Send pywebview's log records through our handlers, which mask tokens.

    pywebview adds a stderr handler of its own when it's imported, and that handler has no
    redaction filter. Without it, records still reach ours by propagating to the root logger.
    """
    logger = logging.getLogger(PYWEBVIEW_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True


def page_location(page: Path, app_root: Path) -> str:
    """The page as pywebview should get it: relative to its app root, or absolute across drives.

    pywebview's server cuts a local path at its first #, so a relative path keeps a # in the
    install folder out of its way.
    """
    try:
        return os.path.relpath(page, app_root)
    except ValueError:
        # Windows can't express a path on another drive relative to this one.
        return str(page)


def is_servable(page: str) -> bool:
    """Whether pywebview's server can serve the page; it cuts a local path at its first #."""
    return "#" not in page


def wait_for_server(address: str, timeout: float = SERVER_WAIT_SECONDS) -> bool:
    """Wait until the server at `address` takes connections. Returns False if it doesn't in time."""
    parts = urlsplit(address)
    target = (parts.hostname, parts.port)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            with socket.create_connection(target, timeout=min(remaining, SERVER_TRY_SECONDS)):
                return True
        except OSError:
            time.sleep(SERVER_PAUSE_SECONDS)


def start_page_server(start_server: Callable[..., tuple[str, Any, Any]], page: str) -> str:
    """Start the page's server and wait until it takes connections. Returns the server's address.

    pywebview marks its server as running before its thread has bound the port, so a window that
    loads at once can get an error page. On macOS, that fires no `loaded` event to retry on.
    """
    address, _, _ = start_server(urls=[page])
    if not wait_for_server(address):
        log.warning("pywebview's server didn't take connections within %s s", SERVER_WAIT_SECONDS)
    return address


def plain_page(message: str, label: str, path: Path) -> str:
    """A self-contained page with a message and the folder it's about."""
    return PLAIN_PAGE.format(
        message=html.escape(message), label=html.escape(label), path=html.escape(str(path))
    )


def show_folder_problem(toolkit: Toolkit, folder: Path) -> int:
    """Show a page that explains the # problem instead of a broken UI. Returns exit code 1."""
    toolkit.create_window(
        WINDOW_TITLE,
        html=plain_page(FOLDER_WITH_HASH, "It's in", folder),
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        background_color=BACKGROUND,
    )
    toolkit.start(private_mode=True)
    return 1


def shows_the_ui(window: Any) -> bool:
    """Whether the window is on the UI's own page and its scripts ran, not on an error page."""
    try:
        location, scmp3_type = window.evaluate_js(PAGE_CHECK)
    except Exception:
        log.warning("Couldn't check which page the window shows", exc_info=True)
        return False
    expected = urlsplit(window.real_url or "")
    actual = urlsplit(str(location))
    same_page = (actual.scheme, actual.netloc, actual.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    )
    return same_page and scmp3_type == "object"


def retry_address(url: str) -> str:
    """The same page under a new address, since WebView2 ignores a load of the address it's on."""
    return urlunsplit(urlsplit(url)._replace(query=RETRY_QUERY))


class PageWatch:
    """Handles pywebview's `loaded` event, which also fires for error pages.

    Once the UI is up, events start. A failed first load is retried once, and a second failure
    shows a plain page instead, so the window is never left on a browser error page.
    """

    def __init__(self, events: EventBus) -> None:
        self._events = events
        # pywebview runs each `loaded` handler on a thread of its own.
        self._lock = threading.Lock()
        self._retried = False
        self.failed = False

    def loaded(self, window: Any) -> None:
        if self.failed:
            return
        if shows_the_ui(window):
            self._events.attach(window)
            return
        with self._lock:
            retry = not self._retried
            self._retried = True
            self.failed = not retry
        if retry:
            log.warning("The window's first load didn't reach the UI, so it's loading it again")
            window.load_url(retry_address(window.real_url))
            return
        log.warning("The UI didn't load on the second try either, so the window explains that")
        window.load_html(plain_page(PAGE_DIDNT_LOAD, "The log is in", paths.log_dir()))


def open_window(
    toolkit: Toolkit,
    api: Api,
    events: EventBus,
    *,
    page: str,
    report_missing_webview2: Callable[[], None] = ask_to_install_webview2,
) -> int:
    """Show the window until it's closed. Returns 1 when the UI couldn't be shown.

    That's when the renderer is MSHTML, when `page` has a #, or when the page failed to load twice.
    """
    # The app never loads files from disk into the window, so a dropped or linked file can't either.
    toolkit.settings["ALLOW_FILE_URLS"] = False
    if not is_servable(page):
        log.warning("The UI's path has a #, which pywebview's server can't handle: %s", page)
        return show_folder_problem(toolkit, WEB_DIR)

    window = toolkit.create_window(
        WINDOW_TITLE,
        page,
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_WINDOW_SIZE,
        background_color=BACKGROUND,
        # base.css makes text unselectable itself, since pywebview's inline rule breaks the CSP.
        text_select=True,
    )
    renderer = RendererCheck()
    window.events.initialized += renderer.accept
    # Events wait until pywebview has injected its bridge into the UI's page.
    watch = PageWatch(events)
    window.events.loaded += watch.loaded
    window.events.closed += events.close
    try:
        toolkit.start(http_server=True, private_mode=True)
    finally:
        events.close()

    if renderer.rejected:
        report_missing_webview2()
        return 1
    return 1 if watch.failed else 0


def run() -> int:
    """Open the main window, wait until it's closed, and return the exit code."""
    setup_logging(console=True)
    register_web_types()
    # Imported here, because importing pywebview adds the log handler that's removed next.
    import webview
    from webview.util import get_app_root

    route_pywebview_logging()
    context = AppContext.from_app_folders()
    page = page_location(INDEX_PAGE, Path(get_app_root()))
    if is_servable(page):
        # webview.start() reuses the server that's already running.
        start_page_server(webview.http.start_global_server, page)
    return open_window(webview, Api(context), context.events, page=page)
