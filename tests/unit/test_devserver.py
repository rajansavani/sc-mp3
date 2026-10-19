"""Tests for `scmp3 --dev-ui`: the server, the types it sends, and the page it opens."""

import threading
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Iterator
from email.message import Message
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from scmp3.__main__ import main
from scmp3.app import devserver


@pytest.fixture(autouse=True)
def no_system_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that would open a page in the user's browser."""

    def refuse(url: str, *args: Any, **kwargs: Any) -> None:
        pytest.fail(f"a test tried to open {url} in the system browser")

    monkeypatch.setattr(webbrowser, "open", refuse)


@pytest.fixture
def server() -> Iterator[ThreadingHTTPServer]:
    dev_server = devserver.make_server()
    # A short poll interval, so shutdown() returns quickly.
    thread = threading.Thread(target=dev_server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield dev_server
    dev_server.shutdown()
    dev_server.server_close()
    thread.join(5)


def fetch(server: ThreadingHTTPServer, path: str) -> tuple[int, Message, bytes]:
    """The status, headers and body of one request. Header names ignore case."""
    host, port = server.server_address[:2]
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/{path}", timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, b""


def test_the_server_listens_on_localhost_only(server: ThreadingHTTPServer) -> None:
    assert server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("index.html", "text/html"),
        ("app.js", "text/javascript"),
        ("vendor/preact.module.js", "text/javascript"),
        ("styles/tokens.css", "text/css"),
        ("fonts/instrument-sans-latin-wght-normal.woff2", "font/woff2"),
    ],
)
def test_web_files_are_served_with_their_types(
    server: ThreadingHTTPServer, path: str, content_type: str
) -> None:
    status, headers, body = fetch(server, path)
    assert status == 200
    assert headers["Content-Type"].split(";")[0] == content_type
    assert body


def test_responses_are_never_cached(server: ThreadingHTTPServer) -> None:
    _, headers, _ = fetch(server, "index.html")
    assert headers["Cache-Control"] == "no-store"


def test_files_outside_the_web_folder_are_not_served(server: ThreadingHTTPServer) -> None:
    status, _, body = fetch(server, "../app/main.py")
    assert status == 404
    assert b"WEB_DIR" not in body


def test_the_page_opens_with_the_mock_api(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    assert devserver.page_url(server) == f"http://127.0.0.1:{port}/index.html?mock"


def test_serve_opens_the_browser_and_stops_on_shutdown(capsys: pytest.CaptureFixture[str]) -> None:
    dev_server = devserver.make_server()
    opened: list[str] = []
    pages: list[int] = []

    def open_browser(url: str) -> None:
        opened.append(url)

        def load_then_stop() -> None:
            with urllib.request.urlopen(url, timeout=5) as response:
                pages.append(response.status)
            dev_server.shutdown()

        threading.Thread(target=load_then_stop, daemon=True).start()

    assert devserver.serve(dev_server, open_browser) == 0
    assert opened == [devserver.page_url(dev_server)]
    assert pages == [200]
    assert opened[0] in capsys.readouterr().err


def test_the_dev_ui_flag_runs_the_dev_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devserver, "run", lambda: 7)
    assert main(["--dev-ui"]) == 7
