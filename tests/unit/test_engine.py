"""Tests for building yt-dlp instances and making API calls through them, without the internet."""

import io
import json
import logging
import os
import secrets
import socket
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yt_dlp
import yt_dlp_ejs
from yt_dlp.extractor.youtube.jsc._builtin import vendor
from yt_dlp.networking.common import Request, Response
from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from scmp3.core import engine, overlay, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import MASK, redact, register_secret

API_URL = "https://api-v2.soundcloud.com/me"


@pytest.fixture
def fake_ffmpeg(scmp3_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty file that SCMP3_FFMPEG points to, so ffmpeg is always "found"."""
    exe = scmp3_home / "bin" / "ffmpeg.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv(paths.FFMPEG_ENV, str(exe))
    return exe


def test_make_ydl_sets_the_standard_options(fake_ffmpeg: Path, scmp3_home: Path) -> None:
    with engine.make_ydl() as ydl:
        params = ydl.params
    assert params["quiet"] is True
    assert params["noprogress"] is True
    assert isinstance(params["logger"], engine.YtDlpLogger)
    assert params["color"] == "never"
    assert params["ffmpeg_location"] == str(fake_ffmpeg)
    assert params["cachedir"] == str(scmp3_home / "cache" / "yt-dlp")
    assert params["socket_timeout"] == 30
    for unset in ("username", "password", "progress_hooks"):
        assert unset not in params


def test_make_ydl_needs_no_network(fake_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("make_ydl tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    with engine.make_ydl(auth_token=f"tok-{secrets.token_hex(8)}") as ydl:
        assert ydl.params["quiet"] is True


def test_a_token_logs_in_as_the_oauth_user_and_is_masked_from_then_on(fake_ffmpeg: Path) -> None:
    token = f"tok-{secrets.token_hex(8)}"
    with engine.make_ydl(auth_token=token) as ydl:
        assert ydl.params["username"] == "oauth"
        assert ydl.params["password"] == token
    assert redact(f"password {token}") == f"password {MASK}"


def test_a_progress_hook_is_registered(fake_ffmpeg: Path) -> None:
    def hook(status: dict[str, Any]) -> None:
        pass

    with engine.make_ydl(progress_hook=hook) as ydl:
        assert ydl.params["progress_hooks"] == [hook]


def test_extra_options_are_added_and_override_the_standard_ones(fake_ffmpeg: Path) -> None:
    with engine.make_ydl(extra={"format": "hls_aac_160k", "socket_timeout": 5}) as ydl:
        assert ydl.params["format"] == "hls_aac_160k"
        assert ydl.params["socket_timeout"] == 5


def test_without_ffmpeg_yt_dlp_runs_without_it(
    scmp3_home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def missing() -> Path:
        raise ScmpError(ErrorKind.convert_failed, "ffmpeg not found")

    monkeypatch.setattr(engine.paths, "ffmpeg_exe", missing)
    with caplog.at_level(logging.WARNING, logger=engine.__name__), engine.make_ydl() as ydl:
        assert "ffmpeg_location" not in ydl.params
    assert "ffmpeg wasn't found" in caplog.text


def test_yt_dlp_messages_reach_python_logging_as_plain_text(
    fake_ffmpeg: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=engine.YTDLP_LOGGER), engine.make_ydl() as ydl:
        ydl.to_screen("[soundcloud] 123: Downloading info JSON")
        ydl.report_warning("Provided authorization token is invalid. Continuing as guest")
        with pytest.raises(DownloadError):
            ydl.report_error("[soundcloud] 123: Unable to download JSON metadata: HTTP Error 500")
    logged = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == engine.YTDLP_LOGGER]
    assert (logging.DEBUG, "[soundcloud] 123: Downloading info JSON") in logged
    assert (
        logging.WARNING,
        "Provided authorization token is invalid. Continuing as guest",
    ) in logged
    # No color codes, since "color" is "never".
    error = "ERROR: [soundcloud] 123: Unable to download JSON metadata: HTTP Error 500"
    assert (logging.ERROR, error) in logged


def test_a_cache_write_that_lost_a_race_is_logged_at_debug_only(
    fake_ffmpeg: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=engine.YTDLP_LOGGER), engine.make_ydl() as ydl:
        ydl.report_warning(
            "Writing cache to 'C:\\cache\\soundcloud\\client_id.json' failed: Traceback ..."
        )
    [record] = [r for r in caplog.records if r.name == engine.YTDLP_LOGGER]
    assert record.levelno == logging.DEBUG
    assert record.getMessage().startswith("Writing cache to")


@pytest.mark.parametrize(
    ("warning", "level"),
    [
        (
            "Unable to extract metadata: ffprobe not found. Please install or provide the path "
            "using --ffmpeg-location",
            logging.DEBUG,
        ),
        ("Unable to extract metadata: ffprobe exited with code 1", logging.WARNING),
    ],
    ids=["no ffprobe", "another metadata failure"],
)
def test_only_the_missing_ffprobe_warning_is_quiet(
    warning: str, level: int, fake_ffmpeg: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=engine.YTDLP_LOGGER), engine.make_ydl() as ydl:
        ydl.report_warning(warning)
    [record] = [r for r in caplog.records if r.name == engine.YTDLP_LOGGER]
    assert record.levelno == level


@pytest.mark.parametrize(
    ("message", "level"),
    [
        ("[soundcloud] 123: This video is DRM protected", logging.INFO),
        ("[youtube] abc: Private video. Sign in if you've been granted access", logging.INFO),
        ("[youtube] abc: Sign in to confirm your age", logging.INFO),
        ("[youtube] abc: This video requires payment to watch", logging.INFO),
        ("[soundcloud] 123: This video is not available from your location", logging.INFO),
        ("[soundcloud] Unable to extract client id", logging.ERROR),
        ("[youtube] abc: Sign in to confirm you're not a bot", logging.ERROR),
        ("[generic] Unsupported URL: https://example.com", logging.ERROR),
    ],
    ids=["drm", "private", "age", "paid", "geo", "client id", "bot check", "unsupported"],
)
def test_errors_for_refusals_sc_mp3_explains_are_logged_at_info(
    message: str, level: int, caplog: pytest.LogCaptureFixture
) -> None:
    logger = engine.YtDlpLogger()
    with caplog.at_level(logging.DEBUG, logger=engine.YTDLP_LOGGER):
        logger.error(f"ERROR: {message}")
        logger.warning(message)
    levels = [r.levelno for r in caplog.records if r.name == engine.YTDLP_LOGGER]
    assert levels == [level, logging.WARNING if level == logging.ERROR else level]


def test_parallel_yt_dlp_instances_writing_the_cache_warn_at_most_at_debug(
    fake_ffmpeg: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The same race as four resolves at once on a first run, with the real cache code.
    instances = [engine.make_ydl() for _ in range(8)]
    barrier = threading.Barrier(len(instances))

    def store(ydl: yt_dlp.YoutubeDL) -> None:
        barrier.wait()
        for number in range(20):
            ydl.cache.store("soundcloud", "client_id", f"id-{number}")

    with caplog.at_level(logging.DEBUG, logger=engine.YTDLP_LOGGER):
        threads = [threading.Thread(target=store, args=(ydl,)) for ydl in instances]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    for ydl in instances:
        ydl.close()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert instances[0].cache.load("soundcloud", "client_id").startswith("id-")


class FakeYdl:
    """Answers urlopen with canned bodies or errors, and records each request."""

    def __init__(self, *outcomes: bytes | Exception) -> None:
        self.requests: list[Request] = []
        self._outcomes = list(outcomes)

    def urlopen(self, request: Request) -> io.BytesIO:
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)


def http_error(status: int, body: bytes = b"") -> HTTPError:
    return HTTPError(Response(io.BytesIO(body), API_URL, {}, status=status))


def api_error(ydl: Any, url: str = API_URL, **kwargs: Any) -> ScmpError:
    """The ScmpError that api_get_json raises for this fake."""
    with pytest.raises(ScmpError) as error_info:
        engine.api_get_json(ydl, url, **kwargs)
    return error_info.value


def test_api_get_json_returns_the_object() -> None:
    ydl = FakeYdl(b'{"username": "kaia", "permalink": "kaia"}')
    assert engine.api_get_json(ydl, API_URL) == {"username": "kaia", "permalink": "kaia"}


def test_api_get_json_impersonates_chrome_and_asks_for_json() -> None:
    ydl = FakeYdl(b"{}")
    engine.api_get_json(ydl, API_URL, headers={"Authorization": "OAuth x"})
    [request] = ydl.requests
    assert request.url == API_URL
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Authorization"] == "OAuth x"
    assert request.extensions["impersonate"] == ImpersonateTarget("chrome")


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (403, ErrorKind.engine_outdated),
        (404, ErrorKind.unavailable),
        (410, ErrorKind.unavailable),
        (429, ErrorKind.rate_limited),
        (500, ErrorKind.service_down),
        (502, ErrorKind.service_down),
        (503, ErrorKind.service_down),
        (599, ErrorKind.service_down),
        (400, ErrorKind.engine_outdated),
        (418, ErrorKind.engine_outdated),
    ],
)
def test_http_errors_become_error_kinds(status: int, kind: ErrorKind) -> None:
    error = api_error(FakeYdl(http_error(status)))
    assert error.kind is kind
    assert error.detail == f"HTTP {status} from api-v2.soundcloud.com/me"


@pytest.mark.parametrize("header", ["Authorization", "authorization"])
def test_a_401_for_a_request_with_a_token_means_the_sign_in_expired(header: str) -> None:
    error = api_error(FakeYdl(http_error(401)), headers={header: "OAuth x"})
    assert error.kind is ErrorKind.auth_expired


def test_a_401_without_a_token_means_the_client_id_is_stale() -> None:
    assert api_error(FakeYdl(http_error(401))).kind is ErrorKind.engine_outdated


def test_a_caller_can_say_what_a_status_means_for_its_endpoint() -> None:
    meanings = {401: ErrorKind.preview_only, 404: ErrorKind.geo_blocked}
    assert api_error(FakeYdl(http_error(401)), status_kinds=meanings).kind is ErrorKind.preview_only
    assert api_error(FakeYdl(http_error(404)), status_kinds=meanings).kind is ErrorKind.geo_blocked
    # Statuses the caller doesn't mention keep their usual meaning.
    assert api_error(FakeYdl(http_error(429)), status_kinds=meanings).kind is ErrorKind.rate_limited


def test_the_error_detail_leaves_out_the_query() -> None:
    url = "https://api-v2.soundcloud.com/resolve?url=https%3A%2F%2Fsoundcloud.com%2Fa%2Fs-AbCd&client_id=x"
    error = api_error(FakeYdl(http_error(404)), url)
    assert error.detail == "HTTP 404 from api-v2.soundcloud.com/resolve"


@pytest.mark.parametrize(
    ("url", "where"),
    [
        ("https://me:hunter22@api-v2.soundcloud.com/me", "api-v2.soundcloud.com/me"),
        ("https://me@api-v2.soundcloud.com:8443/me", "api-v2.soundcloud.com:8443/me"),
        # A port that isn't a number is dropped rather than failing the error itself.
        ("https://api-v2.soundcloud.com:abc/me", "api-v2.soundcloud.com/me"),
    ],
)
def test_the_error_detail_leaves_out_a_user_name_and_password(url: str, where: str) -> None:
    error = api_error(FakeYdl(http_error(404)), url)
    assert error.detail == f"HTTP 404 from {where}"


def test_a_network_failure_is_offline() -> None:
    error = api_error(FakeYdl(TransportError(msg="Could not resolve host")))
    assert error.kind is ErrorKind.offline


@pytest.mark.parametrize("body", [b"<html>blocked</html>", b"[1, 2]", b""])
def test_a_body_that_isnt_a_json_object_means_the_engine_needs_an_update(body: bytes) -> None:
    assert api_error(FakeYdl(body)).kind is ErrorKind.engine_outdated


TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
FORM = {"grant_type": "refresh_token", "refresh_token": "abc def&x=1"}


def post_error(ydl: Any, **kwargs: Any) -> ScmpError:
    """The ScmpError that api_post_form raises for this fake."""
    with pytest.raises(ScmpError) as error_info:
        engine.api_post_form(ydl, TOKEN_URL, FORM, **kwargs)
    return error_info.value


def test_api_post_form_returns_the_object() -> None:
    ydl = FakeYdl(b'{"access_token": "x", "expires_in": 86399}')
    assert engine.api_post_form(ydl, TOKEN_URL, FORM) == {"access_token": "x", "expires_in": 86399}


def test_api_post_form_sends_the_form_encoded_and_impersonates_chrome() -> None:
    ydl = FakeYdl(b"{}")
    engine.api_post_form(ydl, TOKEN_URL, FORM)
    [request] = ydl.requests
    assert request.url == TOKEN_URL
    assert request.method == "POST"
    assert request.data == b"grant_type=refresh_token&refresh_token=abc+def%26x%3D1"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert request.headers["Accept"] == "application/json"
    assert request.extensions["impersonate"] == ImpersonateTarget("chrome")


def test_an_error_status_carries_the_status_and_the_json_in_its_body() -> None:
    body = b'{"error": "invalid_grant", "error_description": "expired"}'
    error = post_error(FakeYdl(http_error(400, body)), status_kinds={400: ErrorKind.auth_expired})
    assert isinstance(error, engine.HttpStatusError)
    assert error.kind is ErrorKind.auth_expired
    assert error.status == 400
    assert error.answer == {"error": "invalid_grant", "error_description": "expired"}
    assert error.detail == "HTTP 400 from secure.soundcloud.com/oauth/token"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        # The form isn't an Authorization header, so a 401 alone doesn't mean the sign-in expired.
        (401, ErrorKind.engine_outdated),
        (403, ErrorKind.engine_outdated),
        (429, ErrorKind.rate_limited),
        (503, ErrorKind.service_down),
    ],
)
def test_a_post_error_status_has_the_usual_meaning(status: int, kind: ErrorKind) -> None:
    assert post_error(FakeYdl(http_error(status))).kind is kind


@pytest.mark.parametrize("body", [b"<html>blocked</html>", b"[1, 2]", b"", b"\xff\xfe"])
def test_an_error_body_that_isnt_a_json_object_gives_an_empty_answer(body: bytes) -> None:
    error = post_error(FakeYdl(http_error(400, body)))
    assert isinstance(error, engine.HttpStatusError)
    assert error.answer == {}


def full_text(error: BaseException) -> str:
    """An exception's message and traceback, with every exception chained to it."""
    return "".join(traceback.format_exception(error))


QUOTING_FAILURES = [
    (lambda text: TransportError(msg=text), ErrorKind.offline, "TransportError"),
    (lambda text: RequestError(msg=text), ErrorKind.engine_outdated, "RequestError"),
]


@pytest.mark.parametrize(("make_error", "kind", "type_name"), QUOTING_FAILURES)
def test_a_post_that_fails_on_the_way_keeps_a_secret_out_of_the_whole_traceback(
    make_error: Callable[[str], Exception], kind: ErrorKind, type_name: str
) -> None:
    secret = secrets.token_hex(16)
    register_secret(secret)
    error = post_error(FakeYdl(make_error(f"connection reset while sending {secret}")))
    assert error.kind is kind
    assert error.detail is not None
    assert "secure.soundcloud.com/oauth/token" in error.detail
    assert type_name in error.detail
    assert secret not in full_text(error)


@pytest.mark.parametrize(("make_error", "kind", "type_name"), QUOTING_FAILURES)
def test_a_get_that_fails_on_the_way_keeps_a_secret_out_of_the_whole_traceback(
    make_error: Callable[[str], Exception], kind: ErrorKind, type_name: str
) -> None:
    secret = secrets.token_hex(16)
    register_secret(secret)
    error = api_error(FakeYdl(make_error(f"connection reset after OAuth {secret}")))
    assert error.kind is kind
    assert error.detail is not None
    assert "api-v2.soundcloud.com/me" in error.detail
    assert type_name in error.detail
    assert secret not in full_text(error)


@pytest.mark.parametrize("body", [b"<html>blocked</html>", b"[1, 2]"])
def test_a_post_answer_that_isnt_a_json_object_means_the_engine_needs_an_update(
    body: bytes,
) -> None:
    assert post_error(FakeYdl(body)).kind is ErrorKind.engine_outdated


class EchoHandler(BaseHTTPRequestHandler):
    """Answers every GET and POST with JSON describing the request it got."""

    # Every path asked for, so a test can check that nothing was sent.
    seen: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self._answer("")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self._answer(self.rfile.read(length).decode())

    def _answer(self, sent: str) -> None:
        self.seen.append(self.path)
        body = json.dumps(
            {
                "path": self.path,
                "method": self.command,
                "body": sent,
                "content_type": self.headers.get("Content-Type", ""),
                "user_agent": self.headers.get("User-Agent", ""),
                "accept": self.headers.get("Accept", ""),
                "custom": self.headers.get("X-Test", ""),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the server quiet in test output."""


@pytest.fixture
def local_server() -> Iterator[str]:
    """An HTTP server on this machine only, so the real opener is tested offline."""
    EchoHandler.seen.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_api_get_json_works_through_the_real_opener(fake_ffmpeg: Path, local_server: str) -> None:
    # An empty proxy means a direct connection, whatever proxy the machine has set.
    with engine.make_ydl(extra={"proxy": ""}) as ydl:
        data = engine.api_get_json(ydl, f"{local_server}/me?x=1", headers={"X-Test": "yes"})
    assert data["path"] == "/me?x=1"
    assert data["accept"] == "application/json"
    assert data["custom"] == "yes"
    # curl_cffi sent Chrome's headers, not yt-dlp's own.
    assert "Chrome/" in data["user_agent"]


def test_api_post_form_works_through_the_real_opener(fake_ffmpeg: Path, local_server: str) -> None:
    with engine.make_ydl(extra={"proxy": ""}) as ydl:
        data = engine.api_post_form(ydl, f"{local_server}/oauth/token", {"a": "1", "b": "x y"})
    assert data["method"] == "POST"
    assert data["path"] == "/oauth/token"
    assert data["body"] == "a=1&b=x+y"
    assert data["content_type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert data["accept"] == "application/json"
    assert "Chrome/" in data["user_agent"]


def test_without_curl_cffi_nothing_is_sent_and_the_engine_needs_an_update(
    fake_ffmpeg: Path, local_server: str
) -> None:
    with engine.make_ydl(extra={"proxy": ""}) as ydl:
        # Removing yt-dlp's curl_cffi handler is what a missing curl_cffi looks like to it.
        ydl._request_director.handlers.pop("CurlCFFI")
        error = api_error(ydl, f"{local_server}/me")
    assert error.kind is ErrorKind.engine_outdated
    assert error.detail is not None
    assert "Impersonate target" in error.detail
    assert EchoHandler.seen == []


def test_engine_version_is_the_loaded_yt_dlp_version() -> None:
    assert engine.engine_version() == yt_dlp.version.__version__


def test_load_yt_dlp_returns_the_module() -> None:
    assert engine.load_yt_dlp() is yt_dlp


def test_load_yt_dlp_turns_plugins_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(overlay.NO_PLUGINS_ENV, raising=False)
    engine.load_yt_dlp()
    assert os.environ[overlay.NO_PLUGINS_ENV] == "1"


# The YouTube scripts guard


@pytest.fixture
def ejs_version(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Sets the version yt-dlp-ejs reports, with the guard's answer worked out again."""

    def set_version(version: str) -> None:
        monkeypatch.setattr(yt_dlp_ejs, "version", version)
        engine.needs_remote_ejs.cache_clear()

    yield set_version
    engine.needs_remote_ejs.cache_clear()


def test_matching_youtube_scripts_need_nothing_from_github(
    fake_ffmpeg: Path, ejs_version: Callable[[str], None]
) -> None:
    ejs_version(f"{vendor.VERSION}.9")
    assert engine.needs_remote_ejs() is False
    with engine.make_ydl() as ydl:
        # yt-dlp keeps the option as a set, empty when nothing was asked for.
        assert ydl.params["remote_components"] == set()


@pytest.mark.parametrize("version", ["0.7.0", "1.8.0", "0.9.0"])
def test_mismatched_youtube_scripts_may_come_from_github(
    fake_ffmpeg: Path, ejs_version: Callable[[str], None], version: str
) -> None:
    ejs_version(version)
    assert vendor.VERSION.startswith("0.8.")
    assert engine.needs_remote_ejs() is True
    with engine.make_ydl() as ydl:
        assert ydl.params["remote_components"] == {"ejs:github"}


def test_the_guard_can_be_overridden_like_any_option(
    fake_ffmpeg: Path, ejs_version: Callable[[str], None]
) -> None:
    ejs_version("0.7.0")
    with engine.make_ydl(extra={"remote_components": []}) as ydl:
        assert ydl.params["remote_components"] == set()


def test_missing_youtube_scripts_may_come_from_github(
    monkeypatch: pytest.MonkeyPatch, ejs_version: Callable[[str], None]
) -> None:
    ejs_version(vendor.VERSION)
    # None in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "yt_dlp_ejs", None)
    engine.needs_remote_ejs.cache_clear()
    assert engine.needs_remote_ejs() is True


# The SoundCloud client_id override


def test_a_client_id_override_is_what_yt_dlp_finds_in_its_cache(fake_ffmpeg: Path) -> None:
    client_id = "a" * 32
    engine.store_client_id(client_id)
    with engine.make_ydl() as ydl:
        assert ydl.cache.load("soundcloud", "client_id") == client_id


def test_clearing_the_override_makes_yt_dlp_find_its_own_again(fake_ffmpeg: Path) -> None:
    engine.store_client_id("a" * 32)
    engine.store_client_id(None)
    with engine.make_ydl() as ydl:
        assert ydl.cache.load("soundcloud", "client_id") is None


def test_the_override_reaches_soundclouds_extractor(fake_ffmpeg: Path) -> None:
    client_id = "b" * 32
    engine.store_client_id(client_id)
    with engine.make_ydl() as ydl:
        extractor = ydl.get_info_extractor("Soundcloud")
        # What yt-dlp does first when it starts using the extractor, before any request.
        extractor._initialize_pre_login()
        assert client_id == extractor._CLIENT_ID
