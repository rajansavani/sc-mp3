"""`scmp3 --dev-ui`: serves web/ on localhost and opens it in a browser, backed by the mock API.

The page is opened with `?mock`, which tells api.js to use web/mock/ instead of waiting for
pywebview's bridge. That bridge only appears after the page loads, so its absence alone can't
tell the browser from the app.
"""

import logging
import sys
import webbrowser
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from scmp3.app.main import WEB_DIR, WEB_TYPES

HOST = "127.0.0.1"
PAGE = "index.html?mock"

log = logging.getLogger(__name__)


class DevRequestHandler(SimpleHTTPRequestHandler):
    """Serves web/ with the right types, and without caching, so a reload shows every edit."""

    extensions_map: ClassVar[dict[str, str]] = {
        **SimpleHTTPRequestHandler.extensions_map,
        **WEB_TYPES,
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("dev server: " + format, *args)


def make_server(web_dir: Path = WEB_DIR, port: int = 0) -> ThreadingHTTPServer:
    """A server for `web_dir` on localhost. Port 0 lets the OS pick a free one."""
    handler = partial(DevRequestHandler, directory=str(web_dir))
    return ThreadingHTTPServer((HOST, port), handler)


def page_url(server: ThreadingHTTPServer) -> str:
    """The address of the UI with the mock API."""
    port = server.server_address[1]
    return f"http://{HOST}:{port}/{PAGE}"


def serve(server: ThreadingHTTPServer, open_browser: Callable[[str], Any]) -> int:
    """Open the UI in a browser and serve it until Ctrl+C or a shutdown. Returns the exit code."""
    url = page_url(server)
    print(f"Serving the UI with sample data at {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)
    open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def run(port: int = 0) -> int:
    """Serve web/ and open it in the default browser."""
    return serve(make_server(WEB_DIR, port), webbrowser.open)
