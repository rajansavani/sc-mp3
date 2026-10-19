"""Tests for the HTTP layer: what open_url sends, and redirects held to https."""

import io
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from scmp3.core import net
from scmp3.core.errors import ErrorKind, ScmpError

LATEST = "https://api.github.com/repos/denoland/deno/releases/latest"
DOWNLOADS = "https://github.com/denoland/deno/releases/download"


class NotModifiedOpener:
    """Stands in for urllib's opener, answering every request with a 304."""

    def __init__(self) -> None:
        self.sent: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> Any:
        self.sent.append(request)
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", Message(), None)


def test_open_url_names_sc_mp3_and_returns_error_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = NotModifiedOpener()
    monkeypatch.setattr(net, "_URL_OPENER", opener)
    response = net.open_url(LATEST, {"If-None-Match": '"abc"'})
    assert response.status == 304
    assert opener.sent[0].get_header("User-agent", "").startswith("sc-mp3/")
    assert opener.sent[0].get_header("If-none-match") == '"abc"'


def test_open_url_follows_redirects_only_to_https() -> None:
    handlers = net._URL_OPENER.handlers
    redirects = [h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert [type(handler) for handler in redirects] == [net.HttpsOnlyRedirects]


def test_a_redirect_from_https_to_http_is_refused() -> None:
    request = urllib.request.Request(f"{DOWNLOADS}/v2.9.7/deno.zip")
    response = io.BytesIO(b"")
    with pytest.raises(ScmpError) as raised:
        net.HttpsOnlyRedirects().redirect_request(
            request, response, 302, "Found", Message(), "http://example.com/deno.zip"
        )
    assert raised.value.kind is ErrorKind.bad_download
    assert response.closed


def test_a_redirect_that_stays_on_https_is_followed() -> None:
    request = urllib.request.Request(f"{DOWNLOADS}/v2.9.7/deno.zip")
    target = "https://release-assets.githubusercontent.com/deno.zip?sig=x"
    followed = net.HttpsOnlyRedirects().redirect_request(
        request, io.BytesIO(b""), 302, "Found", Message(), target
    )
    assert followed is not None
    assert followed.full_url == target
