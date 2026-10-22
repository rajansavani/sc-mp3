"""Tests for how the page's path reaches pywebview's server, from install folders with odd names.

pywebview's server cuts a local path at its first #, so these run its real server code, without a
window, on folders named like the ones people have.
"""

import os
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urljoin

import pytest
import webview.http
import webview.util

from scmp3.app import page_loading

INSTALL_FOLDERS = ["C# projects", "100% done", "with  spaces", "Müsik ünïcode 音楽", "C# 100% Mü"]


def make_install(root: Path) -> tuple[Path, Path]:
    """A checkout-like layout: the web folder, and the venv's Scripts folder as the app root."""
    web = root / "sc-mp3" / "src" / "scmp3" / "web"
    (web / "styles").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>sc-mp3</title>", encoding="utf-8")
    (web / "styles" / "base.css").write_text("body { margin: 0; }", encoding="utf-8")
    # A file above the web folder, which the server must not hand out.
    (root / "outside.txt").write_text("private", encoding="utf-8")
    app_root = root / "sc-mp3" / ".venv" / "Scripts"
    app_root.mkdir(parents=True)
    return web, app_root


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


@pytest.fixture
def unused_port() -> Iterator[socket.socket]:
    """A socket bound to a free local port that doesn't listen yet, so connections are refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    yield sock
    sock.close()


def address_of(sock: socket.socket) -> str:
    return f"http://127.0.0.1:{sock.getsockname()[1]}/"


def test_waiting_for_the_server_ends_when_it_starts_listening(unused_port: socket.socket) -> None:
    starts_listening = threading.Timer(0.3, unused_port.listen)
    started = time.monotonic()
    starts_listening.start()
    try:
        assert page_loading.wait_for_server(address_of(unused_port))
    finally:
        starts_listening.join()
    assert time.monotonic() - started >= 0.3


def test_waiting_for_a_server_that_never_listens_gives_up_in_time(
    unused_port: socket.socket,
) -> None:
    started = time.monotonic()
    assert not page_loading.wait_for_server(address_of(unused_port), timeout=0.5)
    assert time.monotonic() - started < 2


def test_the_apps_page_server_takes_connections_once_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web, app_root = make_install(tmp_path)
    monkeypatch.setattr(webview.util, "get_app_root", lambda: str(app_root))
    location = page_loading.page_location(web / "index.html", app_root)
    # pywebview's start_server, which is start_global_server without setting the global server.
    address = page_loading.start_page_server(webview.http.start_server, location)
    # No waiting here: start_page_server has done it.
    assert fetch(urljoin(address, "index.html"))[0] == 200


def test_the_page_is_relative_to_the_app_root(tmp_path: Path) -> None:
    web, app_root = make_install(tmp_path)
    location = page_loading.page_location(web / "index.html", app_root)
    assert not os.path.isabs(location)
    assert (app_root / location).resolve() == (web / "index.html").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has drive letters")
def test_a_page_on_another_drive_stays_absolute() -> None:
    page = Path(r"D:\sc-mp3\web\index.html")
    assert page_loading.page_location(page, Path(r"C:\Python\Scripts")) == str(page)


@pytest.mark.parametrize("folder", INSTALL_FOLDERS)
def test_pywebviews_server_finds_the_page_in_an_odd_install_folder(
    folder: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web, app_root = make_install(tmp_path / folder)
    monkeypatch.setattr(webview.util, "get_app_root", lambda: str(app_root))
    location = page_loading.page_location(web / "index.html", app_root)

    address, common_path, server = webview.http.BottleServer.start_server([location], None)
    # pywebview reports the server as running before its thread has bound the port.
    assert page_loading.wait_for_server(address), (
        f"pywebview's server at {address} took no connections"
    )
    # The same steps as pywebview's Window._resolve_url.
    page_url = urljoin(address, os.path.relpath(location, common_path))

    assert Path(server.root_path) == web
    assert fetch(page_url) == (200, b"<!doctype html><title>sc-mp3</title>")
    assert fetch(urljoin(page_url, "styles/base.css"))[0] == 200
    assert fetch(urljoin(address, "../outside.txt"))[0] == 404
