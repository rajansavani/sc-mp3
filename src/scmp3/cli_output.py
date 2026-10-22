"""What the command line prints, and the exit codes its commands return."""

from datetime import datetime

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ErrorPlace, ErrorReason, ScmpError
from scmp3.core.log import LOG_FILE
from scmp3.core.models import HistoryEntry, Settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_STOPPED = 130  # what shells report for Ctrl+C

NOT_IMPLEMENTED = "Not implemented yet."
NOTHING_TO_SAVE = "Give at least one SoundCloud link."
TITLE_NEEDS_ONE_LINK = "--title only works with one link, since every track would get it."
FFMPEG_MISSING = (
    "ffmpeg wasn't found. Install it and put it on your PATH, or set SCMP3_FFMPEG to its full path."
)
NO_LOG = "The log file can't be written, so this run isn't logged ({detail})."
INPUT_ENDED = "The input ended before you answered, so nothing more was saved."
STOPPED = "Stopped. The track in progress wasn't saved."
SKIPPED = "Skipped."
SKIPPED_NAME_TAKEN = "Skipped, since a file with that name already exists."
SAVE_AGAIN_WITH_REVIEW = "Run the command without --yes to save it again."
COVER_NOT_DOWNLOADED = "Saved without a cover: the cover couldn't be downloaded."
# Why a file was saved without its cover, by the kind of the cover's failure.
COVER_PROBLEMS: dict[ErrorKind, str] = {
    ErrorKind.convert_failed: "Saved without a cover: the cover image couldn't be read.",
    ErrorKind.disk: "Saved without a cover: the cover file couldn't be read.",
}

# What each error kind means for you, and what to do next, in PLAN §4.11's wording where it has
# one. The app's own wording is in web/strings.
ERROR_MESSAGES: dict[ErrorKind, str] = {
    ErrorKind.drm: (
        "This track is copy-protected by its label, so it can't be saved. "
        "Signing in won't change that."
    ),
    ErrorKind.preview_only: "Only a 30-second preview is available: this is a Go+ track.",
    ErrorKind.unavailable: (
        "This track is private or was removed. If you have a private share link, use that instead."
    ),
    ErrorKind.geo_blocked: "This track isn't available in your country.",
    ErrorKind.rate_limited: "SoundCloud asked us to slow down. Try again in a minute.",
    ErrorKind.auth_expired: "Your SoundCloud sign-in expired. Sign in again.",
    ErrorKind.engine_outdated: (
        "SoundCloud changed something on their end. "
        "Updating the download engine (yt-dlp) usually fixes this."
    ),
    ErrorKind.bot_check: (
        "YouTube wants to confirm you're not a bot. This usually clears up after a while."
    ),
    ErrorKind.offline: "You're offline. Check your connection and try again.",
    ErrorKind.convert_failed: (
        "The audio couldn't be converted. Try again, and if it fails again, "
        "the log has the details: {log}"
    ),
    ErrorKind.disk: "Can't save to {folder}. Choose another folder with --out.",
    ErrorKind.duplicate: "Already in your library (saved {saved}).",
    ErrorKind.service_down: "SoundCloud isn't responding right now. Try again later.",
    ErrorKind.bad_download: (
        "A download sc-mp3 needed was damaged, so it wasn't installed. Try again."
    ),
    ErrorKind.internal: (
        "sc-mp3 ran into a problem it didn't expect. The log has the details: {log}"
    ),
}
# Messages for a kind when it happened somewhere other than the library, by kind and place.
PLACE_MESSAGES: dict[tuple[ErrorKind, ErrorPlace], str] = {
    (ErrorKind.disk, "data"): (
        "sc-mp3 couldn't write to its own data folder. "
        "Check that the drive has free space, then try again."
    ),
}
# Messages for a failure that says more exactly what went wrong, ahead of the kind and place.
REASON_MESSAGES: dict[ErrorReason, str] = {
    "file_in_use": "The file is open in another program or is read-only. Close it and try again.",
    "path_too_long": (
        "The folder path is too long for this file. "
        "Choose a folder closer to the top of the drive with --out."
    ),
}


def saved_on(entry: HistoryEntry) -> str:
    """When a track was saved, like "Oct 3", with the year when it isn't this year."""
    if entry.saved_at is None:
        return "before"
    when = entry.saved_at.astimezone()
    day = f"{when:%b} {when.day}"
    return day if when.year == datetime.now().year else f"{day}, {when.year}"


def error_message(error: ScmpError, settings: Settings) -> str:
    """The message for a failure's reason, or else its kind and place, with the blanks filled in."""
    message = ERROR_MESSAGES[error.kind]
    if error.where is not None:
        message = PLACE_MESSAGES.get((error.kind, error.where), message)
    if error.reason is not None:
        message = REASON_MESSAGES[error.reason]
    return message.format(
        folder=settings.library_dir, log=paths.log_dir() / LOG_FILE, saved="before"
    )
