"""Tests for turning yt-dlp's exceptions into error kinds, using yt-dlp's own error wrapping."""

import errno
import io
import socket
import ssl
from collections.abc import Callable
from typing import Any

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import (
    ContentTooShortError,
    DownloadError,
    ExtractorError,
    UnavailableVideoError,
)

from scmp3.core import engine
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import MASK
from scmp3.core.sources import ytdlp_errors

type Failure = Callable[[InfoExtractor], None]


class QuietLogger:
    """Drops yt-dlp's messages, so expected errors don't clutter the test output."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


class FailingIE(InfoExtractor):
    """An extractor that fails the way a test asks, so yt-dlp wraps the error as it really does."""

    _VALID_URL = r"scmp3test:(?P<id>\w+)"
    IE_NAME = "scmp3test"

    def __init__(self, failure: Failure) -> None:
        super().__init__()
        self._failure = failure

    def _real_extract(self, url: str) -> dict[str, Any]:
        self._failure(self)
        raise AssertionError("the failure didn't raise")


def quiet_ydl() -> YoutubeDL:
    return YoutubeDL({"quiet": True, "logger": QuietLogger()}, auto_init=False)


def raised_by_yt_dlp(failure: Failure) -> DownloadError:
    """The DownloadError that extract_info raises when the extractor fails this way."""
    ydl = quiet_ydl()
    ydl.add_info_extractor(FailingIE(failure))
    with pytest.raises(DownloadError) as error_info:
        ydl.extract_info("scmp3test:abc", download=False)
    return error_info.value


def reported(error: Exception, message: str | None = None) -> DownloadError:
    """What report_error raises while `error` is handled, as yt-dlp's download step does."""
    try:
        raise error
    except Exception as handled:
        with pytest.raises(DownloadError) as error_info:
            quiet_ydl().report_error(message or str(handled))
    return error_info.value


def write_fails_with(os_error: OSError) -> UnavailableVideoError:
    """What YoutubeDL.process_info raises when saving a download fails with this OSError."""
    try:
        try:
            raise os_error
        except OSError as error:
            # yt-dlp raises it like this, so the OSError is only the context.
            raise UnavailableVideoError(error)  # noqa: B904
    except UnavailableVideoError as unavailable:
        return unavailable


def http_error(status: int) -> HTTPError:
    return HTTPError(
        Response(io.BytesIO(b""), "https://api-v2.soundcloud.com/x", {}, status=status)
    )


def request_fails_with(status: int) -> Failure:
    """A request that gets this status, wrapped the way _download_json wraps it."""

    def failure(ie: InfoExtractor) -> None:
        try:
            raise http_error(status)
        except HTTPError as error:
            raise ExtractorError(
                f"Unable to download JSON metadata: {error}", cause=error
            ) from None

    return failure


def request_cant_connect(ie: InfoExtractor) -> None:
    try:
        raise TransportError(msg="Could not resolve host: api-v2.soundcloud.com")
    except TransportError as error:
        raise ExtractorError(f"Unable to download JSON metadata: {error}", cause=error) from None


def test_a_drm_only_track_is_drm() -> None:
    error = raised_by_yt_dlp(lambda ie: ie.report_drm("123"))
    assert ytdlp_errors.error_kind(error) is ErrorKind.drm


def test_a_track_blocked_in_this_country_is_geo_blocked() -> None:
    error = raised_by_yt_dlp(lambda ie: ie.raise_geo_restricted())
    assert ytdlp_errors.error_kind(error) is ErrorKind.geo_blocked


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (404, ErrorKind.unavailable),
        (410, ErrorKind.unavailable),
        (429, ErrorKind.rate_limited),
        # yt-dlp already retried these with a fresh client_id.
        (401, ErrorKind.engine_outdated),
        (403, ErrorKind.engine_outdated),
        (500, ErrorKind.service_down),
        (503, ErrorKind.service_down),
        (400, ErrorKind.engine_outdated),
    ],
)
def test_http_statuses_become_error_kinds(status: int, kind: ErrorKind) -> None:
    error = raised_by_yt_dlp(request_fails_with(status))
    assert ytdlp_errors.error_kind(error) is kind


def test_a_network_failure_is_offline() -> None:
    error = raised_by_yt_dlp(request_cant_connect)
    assert ytdlp_errors.error_kind(error) is ErrorKind.offline


@pytest.mark.parametrize(
    "cause",
    [ConnectionResetError("reset"), TimeoutError("timed out"), socket.gaierror("no host")],
)
def test_standard_library_network_errors_under_yt_dlp_are_offline(cause: Exception) -> None:
    def failure(ie: InfoExtractor) -> None:
        raise ExtractorError("A network error has occurred.", cause=cause, expected=True)

    assert ytdlp_errors.error_kind(raised_by_yt_dlp(failure)) is ErrorKind.offline


def test_a_client_id_that_cant_be_found_means_the_engine_is_outdated() -> None:
    def failure(ie: InfoExtractor) -> None:
        raise ExtractorError("Unable to extract client id")

    assert ytdlp_errors.error_kind(raised_by_yt_dlp(failure)) is ErrorKind.engine_outdated


def test_an_extractor_crash_means_the_engine_is_outdated() -> None:
    def failure(ie: InfoExtractor) -> None:
        raise KeyError("media")  # yt-dlp turns this into "An extractor error has occurred."

    assert ytdlp_errors.error_kind(raised_by_yt_dlp(failure)) is ErrorKind.engine_outdated


def test_a_status_named_only_in_the_message_still_counts() -> None:
    error = DownloadError("ERROR: unable to download video data: HTTP Error 429: Too Many Requests")
    assert ytdlp_errors.error_kind(error) is ErrorKind.rate_limited


def test_the_openers_own_errors_are_mapped_too() -> None:
    # resolve_short_link raises what ydl.urlopen raised, unwrapped.
    assert ytdlp_errors.error_kind(http_error(404)) is ErrorKind.unavailable
    assert ytdlp_errors.error_kind(http_error(502)) is ErrorKind.service_down
    assert ytdlp_errors.error_kind(TransportError(msg="timed out")) is ErrorKind.offline


def test_an_exception_that_isnt_yt_dlps_is_internal() -> None:
    assert ytdlp_errors.error_kind(KeyError("title")) is ErrorKind.internal


def test_a_chain_that_loops_still_ends() -> None:
    error = ExtractorError("first")
    error.cause = ExtractorError("second", cause=error)
    assert ytdlp_errors.error_kind(error) is ErrorKind.engine_outdated


def test_the_detail_is_yt_dlps_message_without_the_error_prefix() -> None:
    error = ytdlp_errors.to_scmp_error(raised_by_yt_dlp(lambda ie: ie.report_drm("123")))
    assert error.kind is ErrorKind.drm
    assert error.detail == "[scmp3test] 123: This video is DRM protected"


def test_the_detail_masks_secret_share_links() -> None:
    message = "ERROR: Unsupported URL: https://soundcloud.com/artist/track/s-AbCdEf12345"
    error = ytdlp_errors.to_scmp_error(DownloadError(message))
    assert error.detail == f"Unsupported URL: https://soundcloud.com/artist/track/s-{MASK}"


# yt-dlp's messages when it needs ffmpeg and can't find it, from its downloaders and fixups.
@pytest.mark.parametrize(
    "message",
    [
        "ERROR: m3u8 download detected but ffmpeg could not be found. Please install",
        "ERROR: Postprocessing: ffmpeg not found. Please install or provide the path using "
        "--ffmpeg-location",
        "ERROR: ffmpeg is not installed",
    ],
)
def test_yt_dlp_missing_ffmpeg_is_ffmpeg_missing_not_an_outdated_engine(message: str) -> None:
    error = ytdlp_errors.to_scmp_error(DownloadError(message))
    assert error.kind is ErrorKind.convert_failed
    assert error.reason == "ffmpeg_missing"


def test_a_scmp_error_passes_through_unchanged() -> None:
    error = ScmpError(ErrorKind.preview_only, "a preview")
    assert ytdlp_errors.to_scmp_error(error) is error


def test_statuses_mean_what_the_engine_says_except_401() -> None:
    for status, kind in engine.STATUS_KINDS.items():
        assert ytdlp_errors.status_kind(status) is kind
    # yt-dlp never sends our token, so its 401 is a stale client_id, never an expired sign-in.
    assert ytdlp_errors.status_kind(401) is ErrorKind.engine_outdated


DISK_ERRORS = [
    OSError(errno.ENOSPC, "No space left on device"),
    PermissionError(errno.EACCES, "Permission denied"),
]


@pytest.mark.parametrize("os_error", DISK_ERRORS)
def test_a_download_that_cant_be_written_is_a_disk_error(os_error: OSError) -> None:
    unavailable = write_fails_with(os_error)
    # download() sees it bare from process_ie_result; yt-dlp's download() reports it first.
    assert ytdlp_errors.error_kind(unavailable) is ErrorKind.disk
    error = ytdlp_errors.to_scmp_error(reported(unavailable))
    assert error.kind is ErrorKind.disk
    assert error.detail == f"Unable to download video: {os_error}"


@pytest.mark.parametrize(
    "network_error",
    [ConnectionResetError("reset"), TimeoutError("timed out"), ssl.SSLError("bad record")],
)
def test_a_lost_connection_behind_a_failed_write_is_offline(network_error: OSError) -> None:
    assert ytdlp_errors.error_kind(write_fails_with(network_error)) is ErrorKind.offline


def test_an_os_error_as_the_cause_is_a_disk_error() -> None:
    def failure(ie: InfoExtractor) -> None:
        raise ExtractorError("Unable to create directory", cause=PermissionError(errno.EACCES, "x"))

    assert ytdlp_errors.error_kind(raised_by_yt_dlp(failure)) is ErrorKind.disk


def test_a_download_cut_short_is_offline() -> None:
    cut_short = ContentTooShortError(1_000_000, 7_943_500)
    # YoutubeDL.process_info words its report like this.
    message = "content too short (expected 7943500 bytes and served 1000000)"
    assert ytdlp_errors.error_kind(reported(cut_short, message)) is ErrorKind.offline


def test_context_is_followed_only_to_find_an_os_error() -> None:
    # An error yt-dlp recovered from, like the 401 before a client_id retry, says nothing.
    try:
        try:
            raise http_error(404)
        except HTTPError:
            raise DownloadError("ERROR: something else went wrong")  # noqa: B904
    except DownloadError as error:
        assert ytdlp_errors.error_kind(error) is ErrorKind.engine_outdated


def test_a_context_hidden_with_from_none_is_not_followed() -> None:
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError:
            raise DownloadError("ERROR: something else went wrong") from None
    except DownloadError as error:
        assert ytdlp_errors.error_kind(error) is ErrorKind.engine_outdated
