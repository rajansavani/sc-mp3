"""Error kinds and the exceptions core raises.

The app's wording for each kind is in `web/strings`, and the command line's is in `cli_output.py`.
"""

from enum import StrEnum
from typing import Literal

# Which folder a disk error was about: the library, sc-mp3's own data, cache and staging, or the
# folder of a file being re-tagged.
ErrorPlace = Literal["library", "data", "file"]
# What went wrong more exactly, for errors whose kind alone would give the wrong advice. The UI
# has text for each.
ErrorReason = Literal[
    # The file being replaced is open in another program, or is read-only.
    "file_in_use",
    # The library folder's path leaves no room for a file name under Windows' path limit.
    "path_too_long",
    # Why a YouTube video is unavailable: only its uploader's invitees, signed-in adults, the
    # channel's members or buyers can watch it, it was taken down, or it's a stream still running.
    "private",
    "age_restricted",
    "members_only",
    "paid",
    "removed",
    "live_stream",
    # The file to re-tag has gone, or its drive isn't connected.
    "file_missing",
    # The file to re-tag isn't MP3 or M4A audio sc-mp3 can read, whatever its extension says.
    "unreadable_file",
    # The file to re-tag is there but can't be read, as when another program holds it.
    "file_locked",
    # Another program changed the file since sc-mp3 read it, so it isn't overwritten.
    "file_changed",
    # mutagen couldn't write the new tags into the copy of a file being re-tagged.
    "tag_write_failed",
    # ffmpeg isn't installed on Linux, or isn't next to the app where the app ships it.
    "ffmpeg_missing",
]


class ErrorKind(StrEnum):
    """Why a job failed. The app picks the message and the next step from this."""

    drm = "drm"
    preview_only = "preview_only"
    unavailable = "unavailable"
    geo_blocked = "geo_blocked"
    rate_limited = "rate_limited"
    auth_expired = "auth_expired"
    engine_outdated = "engine_outdated"
    bot_check = "bot_check"
    offline = "offline"
    convert_failed = "convert_failed"
    disk = "disk"
    duplicate = "duplicate"
    # SoundCloud or YouTube answered with a 5xx error.
    service_down = "service_down"
    # A downloaded component didn't match its checksum or didn't run, so it wasn't installed.
    bad_download = "bad_download"
    # A YouTube link needs Deno, which isn't installed yet. Installing it retries the job.
    youtube_support_missing = "youtube_support_missing"
    # An unexpected exception in sc-mp3's own code.
    internal = "internal"


class ScmpError(Exception):
    """A failure of a known kind, with an optional detail for the logs.

    `where` says which folder a disk error was about. None counts as the library. `reason` says
    more exactly what went wrong, when the kind's own advice wouldn't fit.
    """

    def __init__(
        self,
        kind: ErrorKind | str,
        detail: str | None = None,
        *,
        where: ErrorPlace | None = None,
        reason: ErrorReason | None = None,
    ) -> None:
        # Converting here rejects a misspelled kind at the point it's raised.
        kind = ErrorKind(kind)
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail
        self.where = where
        self.reason = reason

    def __str__(self) -> str:
        if self.detail:
            return f"{self.kind}: {self.detail}"
        return str(self.kind)


class Cancelled(Exception):
    """Raised when the user cancels a job while a step is running."""
