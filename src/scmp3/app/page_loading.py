"""Loading the UI's page into the window: where it's served from, waiting for the server, and
telling the UI from an error page, with one retry and then a plain page."""

import html
import logging
import os
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import bottle

from scmp3.app.events import EventBus
from scmp3.core import paths

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

# Where the window is, whether the UI's scripts ran there, and whether the page is the UI's own
# HTML, which has the #app element the UI renders into. api.js defines window.scmp3.
PAGE_CHECK = '[window.location.href, typeof window.scmp3, document.getElementById("app") !== null]'
# Added to the page's address for the one retry. The UI ignores it.
RETRY_QUERY = "retry"
# How long the UI's scripts may take to start once its page has loaded, and how often to look.
# WKWebView reports the load before the page's modules have run.
UI_START_SECONDS = 10.0
UI_POLL_SECONDS = 0.05

# How long to wait for pywebview's server to take connections, and how long each try may take.
# Each try is short, since Windows takes 2 s to refuse a connection to a closed local port.
SERVER_WAIT_SECONDS = 5.0
SERVER_TRY_SECONDS = 0.1
SERVER_PAUSE_SECONDS = 0.05

log = logging.getLogger(__name__)


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


class LoopbackServer(socketserver.ThreadingMixIn, WSGIServer):
    """pywebview's threaded server, without http.server's lookup of the host's name."""

    def server_bind(self) -> None:
        # HTTPServer names itself with socket.getfqdn, a reverse lookup that macOS 15 counts as
        # finding devices on the local network: it asks the user, and the server waits.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
        self.setup_environ()


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args: Any, **kwargs: Any) -> None:
        """Log nothing per request, like pywebview's server outside debug mode."""


class LoopbackAdapter(bottle.ServerAdapter):
    """Runs pywebview's server the way pywebview does, but on LoopbackServer."""

    def run(self, handler: Any) -> None:
        if self.quiet:
            self.options["handler_class"] = QuietHandler
        server = make_server(
            self.host, self.port, handler, server_class=LoopbackServer, **self.options
        )
        server.serve_forever()


def serve_without_name_lookup(http_module: Any) -> None:
    """Make pywebview's server skip the host-name lookup that macOS 15 asks the user about."""
    # pywebview looks its server adapter up by this name each time it starts a server.
    http_module.ThreadedAdapter = LoopbackAdapter


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


def is_the_ui_address(location: str, ui_url: str | None) -> bool:
    """Whether the page's own address is the UI's, whatever its query."""
    actual = urlsplit(location)
    expected = urlsplit(ui_url or "")
    return (actual.scheme, actual.netloc, actual.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    )


def shows_the_ui(window: Any, closed: threading.Event | None = None) -> bool:
    """Whether the window is on the UI's own page and its scripts ran, not on an error page.

    On the UI's own HTML it waits up to UI_START_SECONDS for the scripts to start, or until
    `closed` is set. Anywhere else, such as on an error page, it answers at once.
    """
    closed = closed or threading.Event()
    deadline = time.monotonic() + UI_START_SECONDS
    while not closed.is_set():
        try:
            location, scmp3_type, ui_html = window.evaluate_js(PAGE_CHECK)
        except Exception:
            log.warning("Couldn't check which page the window shows", exc_info=True)
            return False
        if not ui_html or not is_the_ui_address(str(location), window.real_url):
            log.info(
                "The window is on %s, which isn't the UI's page at %s", location, window.real_url
            )
            return False
        if scmp3_type == "object":
            return True
        if time.monotonic() >= deadline:
            log.info("The UI's scripts didn't start within %s s at %s", UI_START_SECONDS, location)
            return False
        closed.wait(UI_POLL_SECONDS)
    return False


def in_background(task: Callable[[], None]) -> None:
    """Run the task on a daemon thread, which can't keep the app from exiting."""
    threading.Thread(target=task, daemon=True).start()


def retry_address(url: str) -> str:
    """The same page under a new address, since WebView2 ignores a load of the address it's on."""
    return urlunsplit(urlsplit(url)._replace(query=RETRY_QUERY))


class PageWatch:
    """Handles pywebview's `loaded` event, which also fires for error pages.

    Once the UI is up, events start and `on_ready` gets the window. A failed first load is retried
    once, and a second failure shows a plain page instead, so the window is never left on a
    browser error page.
    """

    def __init__(self, events: EventBus, on_ready: Callable[[Any], None] | None = None) -> None:
        self._events = events
        self._on_ready = on_ready
        # pywebview runs each `loaded` handler on a thread of its own.
        self._lock = threading.Lock()
        self._retried = False
        self._closed = threading.Event()
        self.failed = False

    def loaded(self, window: Any) -> None:
        # evaluate_js can wait forever once the window has closed, and pywebview's own handler
        # threads would then keep the app from exiting.
        in_background(lambda: self.check(window))

    def window_closed(self) -> None:
        """Stop a check that's waiting for the UI, and start no retry."""
        self._closed.set()

    def check(self, window: Any) -> None:
        """Start events once the window shows the UI, or retry the load, or show the plain page."""
        if self.failed or self._closed.is_set():
            return
        if shows_the_ui(window, self._closed):
            # macOS reports no failed loads, so this line is how a log shows the UI came up.
            log.info("The window shows the UI")
            self._events.attach(window)
            if self._on_ready is not None:
                self._on_ready(window)
            return
        if self._closed.is_set():
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
