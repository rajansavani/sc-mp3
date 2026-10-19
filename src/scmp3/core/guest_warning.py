"""Notices yt-dlp's "Continuing as guest" warning, which is all it gives for a token that fails."""

import logging
import threading
from types import TracebackType
from typing import Self

from scmp3.core.engine import YTDLP_LOGGER

# yt-dlp's warning when the token it was given fails and it carries on signed out.
GUEST_WARNING = "continuing as guest"


class GuestWarningWatcher(logging.Handler):
    """Notices yt-dlp's "Continuing as guest" warning on this thread, so the caller can renew.

    With a token that fails, yt-dlp carries on signed out instead of raising. Use it as a context
    manager around the yt-dlp call, then check `saw_guest_warning`.
    """

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        # Other jobs log to the same logger from their own threads.
        self._thread = threading.get_ident()
        self.saw_guest_warning = False

    def emit(self, record: logging.LogRecord) -> None:
        """Note the guest warning when this thread logs it."""
        if record.thread != self._thread:
            return
        try:
            message = record.getMessage()
        except Exception:
            # Logging's own way to report a record that can't be formatted.
            self.handleError(record)
            return
        if GUEST_WARNING in message.lower():
            self.saw_guest_warning = True

    def __enter__(self) -> Self:
        logging.getLogger(YTDLP_LOGGER).addHandler(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        logging.getLogger(YTDLP_LOGGER).removeHandler(self)
