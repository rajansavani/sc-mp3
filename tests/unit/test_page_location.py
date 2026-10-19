"""Tests for how the page's path reaches pywebview's server, from install folders with odd names.

pywebview's server cuts a local path at its first #, so these run its real server code, without a
window, on folders named like the ones people have.
"""

import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import pytest
import webview.http
import webview.util

from scmp3.app import main

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


def test_the_page_is_relative_to_the_app_root(tmp_path: Path) -> None:
    web, app_root = make_install(tmp_path)
    location = main.page_location(web / "index.html", app_root)
    assert not os.path.isabs(location)
    assert (app_root / location).resolve() == (web / "index.html").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has drive letters")
def test_a_page_on_another_drive_stays_absolute() -> None:
    page = Path(r"D:\sc-mp3\web\index.html")
    assert main.page_location(page, Path(r"C:\Python\Scripts")) == str(page)


@pytest.mark.parametrize("folder", INSTALL_FOLDERS)
def test_pywebviews_server_finds_the_page_in_an_odd_install_folder(
    folder: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web, app_root = make_install(tmp_path / folder)
    monkeypatch.setattr(webview.util, "get_app_root", lambda: str(app_root))
    location = main.page_location(web / "index.html", app_root)

    address, common_path, server = webview.http.BottleServer.start_server([location], None)
    # The same steps as pywebview's Window._resolve_url.
    page_url = urljoin(address, os.path.relpath(location, common_path))

    assert Path(server.root_path) == web
    assert fetch(page_url) == (200, b"<!doctype html><title>sc-mp3</title>")
    assert fetch(urljoin(page_url, "styles/base.css"))[0] == 200
    assert fetch(urljoin(address, "../outside.txt"))[0] == 404
