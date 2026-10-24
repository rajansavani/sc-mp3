"""Tests for YouTube's refusals in ytdlp_errors: each becomes its kind, with a reason when the
kind alone would give the wrong advice. The messages are the ones yt-dlp passes on."""

import pytest
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError

from scmp3.core.errors import ErrorKind, ErrorReason
from scmp3.core.sources import ytdlp_errors

VIDEO = "[youtube] y9qO_xgE34Q"
HINT = "Use --cookies-from-browser or --cookies for the authentication"


def reported(message: str, inner_type: type[ExtractorError] = ExtractorError) -> DownloadError:
    """What yt-dlp raises out of extract_info for a refusal it expected."""
    inner = inner_type(f"{VIDEO}: {message}", expected=True)
    return DownloadError(f"ERROR: {inner}", exc_info=(type(inner), inner, None))


@pytest.mark.parametrize(
    ("message", "kind", "reason"),
    [
        (f"Sign in to confirm you\u2019re not a bot. {HINT}", ErrorKind.bot_check, None),
        (f"Sign in to confirm you're not a bot. {HINT}", ErrorKind.bot_check, None),
        (
            "Sign in to confirm your age. This video may be inappropriate for some users.",
            ErrorKind.unavailable,
            "age_restricted",
        ),
        (
            "Join this channel to get access to members-only content like this video, and other "
            "exclusive perks.",
            ErrorKind.unavailable,
            "members_only",
        ),
        (
            "This video is available to this channel's members on level: Fan (or any higher "
            "level).",
            ErrorKind.unavailable,
            "members_only",
        ),
        (
            f"Private video. Sign in if you've been granted access to this video. {HINT}",
            ErrorKind.unavailable,
            "private",
        ),
        (
            "Video unavailable. This video has been removed by the uploader",
            ErrorKind.unavailable,
            "removed",
        ),
        (
            "Video unavailable. This video is no longer available because the YouTube account "
            "associated with this video has been terminated.",
            ErrorKind.unavailable,
            "removed",
        ),
        ("This live event will begin in 3 hours.", ErrorKind.unavailable, "live_stream"),
        ("Premieres in 2 days", ErrorKind.unavailable, "live_stream"),
        (
            "Video unavailable. This content isn't available, try again later. The current "
            "session has been rate-limited by YouTube for up to an hour.",
            ErrorKind.rate_limited,
            None,
        ),
        ("This video is DRM protected", ErrorKind.drm, None),
        # The messages the review met live or with yt-dlp, word for word.
        ("This video is unavailable", ErrorKind.unavailable, "removed"),
        ("This video is not available", ErrorKind.unavailable, "removed"),
        ("This video isn't available anymore", ErrorKind.unavailable, "removed"),
        ("This live stream recording is not available.", ErrorKind.unavailable, "removed"),
        ("This video requires payment to watch.", ErrorKind.unavailable, "paid"),
        (
            "All player responses are invalid. Your IP is likely being blocked by Youtube",
            ErrorKind.bot_check,
            None,
        ),
        (
            "Video unavailable. This video contains content from SME, who has blocked it in "
            "your country on copyright grounds.",
            ErrorKind.geo_blocked,
            None,
        ),
    ],
    ids=[
        "bot check",
        "bot check, straight apostrophe",
        "age-restricted",
        "members only",
        "members on a level",
        "private",
        "removed",
        "account terminated",
        "live event",
        "premiere",
        "rate limited",
        "drm",
        "unavailable",
        "not available",
        "not available anymore",
        "no recording",
        "paid",
        "ip blocked",
        "blocked here on copyright grounds",
    ],
)
def test_youtubes_refusals_get_their_kind_and_reason(
    message: str, kind: ErrorKind, reason: ErrorReason | None
) -> None:
    error = ytdlp_errors.to_scmp_error(reported(message))
    assert (error.kind, error.reason) == (kind, reason)
    assert ytdlp_errors.error_kind(reported(message)) is kind
    assert error.detail and error.detail.startswith(VIDEO)


def test_a_video_blocked_in_this_country_is_geo_blocked() -> None:
    message = "The uploader has not made this video available in your country"
    error = ytdlp_errors.to_scmp_error(reported(message, GeoRestrictedError))
    assert (error.kind, error.reason) == (ErrorKind.geo_blocked, None)


def test_a_private_video_isnt_read_as_merely_unavailable() -> None:
    # YouTube can open a private video's message the way it opens a removed one's.
    error = ytdlp_errors.to_scmp_error(reported("Video unavailable. This video is private."))
    assert error.reason == "private"


def test_soundclouds_messages_keep_their_kinds_without_a_reason() -> None:
    inner = ExtractorError("[soundcloud] 123: This video is DRM protected", expected=True)
    error = ytdlp_errors.to_scmp_error(inner)
    assert (error.kind, error.reason) == (ErrorKind.drm, None)
