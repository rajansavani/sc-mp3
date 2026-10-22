"""Loading the UI's page into the window: where it's served from, waiting for the server, and
telling the UI from an error page, with one retry and then a plain page."""

import html
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

# Where the window is, and whether the UI's scripts ran there; api.js defines window.scmp3.
PAGE_CHECK = "[window.location.href, typeof window.scmp3]"
# Added to the page's address for the one retry. The UI ignores it.
RETRY_QUERY = "retry"

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
        self.failed = False

    def loaded(self, window: Any) -> None:
        if self.failed:
            return
        if shows_the_ui(window):
            self._events.attach(window)
            if self._on_ready is not None:
                self._on_ready(window)
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
