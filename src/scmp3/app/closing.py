"""Asking before the window closes while tracks in the queue haven't been saved, if closing would
lose them.

queue.json brings the queue back at the next start, so closing normally asks nothing. Only when
queue.json can't be written does closing drop the tracks, and then the user decides.
"""

import ctypes
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from scmp3.app import messages
from scmp3.app.message_box import (
    IDYES,
    MB_DEFBUTTON2,
    MB_ICONWARNING,
    MB_TASKMODAL,
    MB_YESNO,
    MessageBox,
    windows_message_box,
)

# Asks a yes or no question, and says whether the answer was yes.
Ask = Callable[[str], bool]
# GetSystemMetrics' index for "this session is logging off or shutting down".
SM_SHUTTINGDOWN = 0x2000

log = logging.getLogger(__name__)


class UnsavedJobs(Protocol):
    """The part of the job manager the question needs."""

    def unsaved_count(self) -> int: ...

    def keep_queue(self) -> bool: ...


@dataclass(frozen=True)
class Asking:
    """How one OS asks, and how it tells that the session is ending, when nobody can answer."""

    ask: Ask
    session_ending: Callable[[], bool]


def ask_on_windows(question: str, message_box: MessageBox | None = None) -> bool:
    """Ask with a native message box over the window, with No as the default answer."""
    # Looked up here rather than as a default, so a test that replaces it is never bypassed.
    show = message_box or windows_message_box
    flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TASKMODAL
    return show(question, messages.UNSAVED_TITLE, flags) == IDYES


def windows_session_ending() -> bool:
    """Whether Windows is logging off or shutting down."""
    return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))


# How each OS asks. Elsewhere the window closes without asking.
# TODO(T-070): Ask on macOS too, where the closing handler runs on the main thread.
# TODO(T-073): Ask on Linux too.
ASKERS: dict[str, Asking] = {"win32": Asking(ask_on_windows, windows_session_ending)}


class CloseGuard:
    """pywebview's closing handler, which keeps the window open if the user says so."""

    def __init__(self, jobs: UnsavedJobs, asking: Asking | None) -> None:
        self._jobs = jobs
        self._asking = asking

    def closing(self) -> bool:
        """Whether the window may close. pywebview cancels the close on False."""
        count = self._jobs.unsaved_count()
        if count == 0 or self._asking is None:
            return True
        # Written now, while the window can still stay open if it fails.
        if self._jobs.keep_queue():
            return True
        if self._asking.session_ending():
            # A question would hold up logging off, and a save placing its file gets 5 s anyway.
            log.info("Windows is ending the session, so the window closed with %d unsaved", count)
            return True
        if self._asking.ask(messages.unsaved(count)):
            log.info("The window closed with %d tracks unsaved", count)
            return True
        log.info("The window stayed open, since %d tracks haven't been saved", count)
        return False


def close_guard(jobs: UnsavedJobs, platform: str = sys.platform) -> CloseGuard:
    """The closing handler for this OS."""
    return CloseGuard(jobs, ASKERS.get(platform))
