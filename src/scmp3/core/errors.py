"""Error kinds and the exceptions core raises. The user-facing text lives in `app/messages.py`."""

from enum import StrEnum


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
    # An unexpected exception in sc-mp3's own code.
    internal = "internal"


class ScmpError(Exception):
    """A failure of a known kind, with an optional detail for the logs."""

    def __init__(self, kind: ErrorKind | str, detail: str | None = None) -> None:
        # Converting here rejects a misspelled kind at the point it's raised.
        kind = ErrorKind(kind)
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    def __str__(self) -> str:
        if self.detail:
            return f"{self.kind}: {self.detail}"
        return str(self.kind)


class Cancelled(Exception):
    """Raised when the user cancels a job while a step is running."""
