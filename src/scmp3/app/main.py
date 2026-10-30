"""Opens the sc-mp3 window, which shows the web UI in web/ through pywebview's built-in server.

page_loading.py gets the page into the window.
"""

import logging
import mimetypes
import webbrowser
from collections.abc import Callable, MutableMapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from scmp3.app import instance, webview_data, window_memory, window_place
from scmp3.app.bridge import Api
from scmp3.app.closing import close_guard
from scmp3.app.context import AppContext
from scmp3.app.drops import FileDrops
from scmp3.app.events import EventBus
from scmp3.app.message_box import IDYES, MB_ICONWARNING, MB_YESNO, MessageBox, windows_message_box
from scmp3.app.page_loading import (
    PageWatch,
    is_servable,
    page_location,
    plain_page,
    serve_without_name_lookup,
    start_page_server,
)
from scmp3.app.webview_data import EarlyDeleteFilter
from scmp3.app.window_place import ScreenOrigin, WindowPlace
from scmp3.core.engine_start import EngineStart, log_start
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
    # Its warning about a data folder still in use is answered by deleting it later.
    if not any(isinstance(item, EarlyDeleteFilter) for item in logger.filters):
        logger.addFilter(EarlyDeleteFilter())


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


def open_window(
    toolkit: Toolkit,
    api: Api,
    events: EventBus,
    *,
    page: str,
    report_missing_webview2: Callable[[], None] = ask_to_install_webview2,
    on_created: Callable[[Any], None] | None = None,
    on_ready: Callable[[Any], None] | None = None,
    on_closing: Callable[[], bool] | None = None,
    data_folder: Path | None = None,
    place: WindowPlace | None = None,
    origin: ScreenOrigin | None = None,
) -> int:
    """Show the window until it's closed. Returns 1 when the UI couldn't be shown.

    That's when the renderer is MSHTML, when `page` has a #, or when the page failed to load twice.
    `on_ready` gets the window each time the UI has loaded, and `on_closing` can keep it open by
    returning False. `data_folder` is where WebView2 keeps its data, instead of the temp folder.
    `place` is where the window opens, as window_fit fitted it to the monitors, or None for
    the default size, placed by the toolkit. `origin` is the screen Qt and GTK place it against.
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
        **window_place.window_options(place, WINDOW_SIZE, origin),
        min_size=MIN_WINDOW_SIZE,
        background_color=BACKGROUND,
        # base.css makes text unselectable itself, since pywebview's inline rule breaks the CSP.
        text_select=True,
    )
    if on_created is not None:
        on_created(window)
    renderer = RendererCheck()
    window.events.initialized += renderer.accept
    # Events wait until pywebview has injected its bridge into the UI's page.
    watch = PageWatch(events, on_ready)
    window.events.loaded += watch.loaded
    if on_closing is not None:
        window.events.closing += on_closing
    window.events.closed += events.close
    window.events.closed += watch.window_closed
    storage = {"storage_path": str(data_folder)} if data_folder is not None else {}
    try:
        toolkit.start(http_server=True, private_mode=True, **storage)
    finally:
        events.close()

    if renderer.rejected:
        report_missing_webview2()
        return 1
    return 1 if watch.failed else 0


def run(paths: Sequence[str] = (), engine_start: EngineStart | None = None) -> int:
    """Open the main window, wait until it's closed, and return the exit code.

    `paths` are files the app was opened with, which go into the queue as if dropped on it.
    `engine_start` says which yt-dlp __main__ settled on before anything imported it.
    """
    engine_start = engine_start or EngineStart()
    setup_logging(console=True)
    log_start(engine_start)
    lock = instance.instance_lock()
    if instance.another_copy_is_open(lock):
        # TODO(T-062): Hand the paths to the copy that's open.
        instance.say_already_open()
        return 1
    try:
        return run_window(paths, engine_start)
    finally:
        lock.release()


def run_window(paths: Sequence[str] = (), engine_start: EngineStart | None = None) -> int:
    """Open the window for this copy of the app, and stop its work once the window closes."""
    register_web_types()
    # Imported here, because importing pywebview adds the log handler that's removed next.
    import webview
    from webview.util import get_app_root

    route_pywebview_logging()
    # What the last window left behind, which nothing holds any more.
    webview_data.remove_data_folder(webview_data.data_folder(), tries=1)
    context = AppContext.from_app_folders(engine_start)
    context.open_stores()
    context.jobs.start_up()
    context.updates.start()
    drops = FileDrops(context.jobs, context.events, context.drag_paths)
    # What isn't added waits in the event bus, and the UI shows it once the page is up.
    drops.add_paths(paths)
    page = page_location(INDEX_PAGE, Path(get_app_root()))
    if is_servable(page):
        serve_without_name_lookup(webview.http)
        # webview.start() reuses the server that's already running.
        start_page_server(webview.http.start_global_server, page)
    # On Windows and Linux. It never raises, so the shutdown below always runs.
    memory = window_memory.for_this_platform(WINDOW_SIZE, MIN_WINDOW_SIZE)

    def on_created(window: Any) -> None:
        context.desktop.attach(window)
        if memory is not None:
            memory.attach(window)

    try:
        api = Api(context)
        return open_window(
            webview,
            api,
            context.events,
            page=page,
            on_created=on_created,
            on_ready=drops.bind,
            on_closing=close_guard(context.jobs).closing,
            data_folder=webview_data.data_folder(),
            place=memory.place if memory is not None else None,
            origin=memory.origin if memory is not None else None,
        )
    finally:
        if memory is not None:
            memory.keep()
        context.updates.stop()
        context.jobs.shut_down()
        webview_data.remove_data_folder(webview_data.data_folder())
