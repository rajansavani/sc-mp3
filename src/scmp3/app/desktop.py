"""The desktop around the window: file dialogs, the file manager, and opening Spotify.

Every file dialog goes through `Desktop.choose`. pywebview #1823 reported WinForms dialogs
hanging when opened from a bridge thread. T-022 checked pywebview 6.2.1 on Windows 11: open and
folder dialogs opened from bridge threads showed, closed and returned, so they're opened there.
"""

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from scmp3.core.errors import ErrorKind, ScmpError

DialogKind = Literal["open", "folder"]
Command = list[str]

# How long opening a link may take on macOS and Linux, where the opener reports failure.
OPEN_TIMEOUT = 10.0

log = logging.getLogger(__name__)


class DialogWindow(Protocol):
    """The part of a pywebview window that shows file dialogs."""

    def create_file_dialog(
        self,
        dialog_type: int = ...,
        directory: str = ...,
        allow_multiple: bool = ...,
        save_filename: str = ...,
        file_types: Sequence[str] = ...,
    ) -> Sequence[str] | None: ...


def pywebview_dialog_type(kind: DialogKind) -> int:
    """pywebview's number for a kind of dialog. Imported here, as only the app window needs it."""
    from webview import FileDialog

    return {"open": FileDialog.OPEN, "folder": FileDialog.FOLDER}[kind]


def _windows_reveal(path: Path, is_file: bool) -> Command:
    # Explorer can't read "/select,<path>" once it's quoted for a space, so the two go apart.
    return ["explorer", "/select,", str(path)] if is_file else ["explorer", str(path)]


def _mac_reveal(path: Path, is_file: bool) -> Command:
    return ["open", "-R", str(path)] if is_file else ["open", str(path)]


def _linux_reveal(path: Path, is_file: bool) -> Command:
    # xdg-open would play a file, so it gets the folder.
    return ["xdg-open", str(path.parent if is_file else path)]


# How each OS shows a file in its file manager. A new OS is one more entry.
REVEAL_COMMANDS: dict[str, Callable[[Path, bool], Command]] = {
    "win32": _windows_reveal,
    "darwin": _mac_reveal,
}
# The command that opens a link such as spotify:, where the OS has no os.startfile.
OPENERS: dict[str, str] = {"darwin": "open"}


def _spawn(command: Command) -> None:
    """Start a program without waiting for it."""
    subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _run_opener(command: Command) -> bool:
    """Run a link opener and say whether it worked."""
    result = subprocess.run(command, capture_output=True, timeout=OPEN_TIMEOUT, check=False)
    return result.returncode == 0


class Desktop:
    """File dialogs on the app's window, and the OS's file manager and link handler."""

    def __init__(
        self,
        *,
        platform: str = sys.platform,
        spawn: Callable[[Command], None] = _spawn,
        run_opener: Callable[[Command], bool] = _run_opener,
        start_file: Callable[[str], Any] | None = None,
        dialog_type: Callable[[DialogKind], int] = pywebview_dialog_type,
    ) -> None:
        self._platform = platform
        self._spawn = spawn
        self._run_opener = run_opener
        self._start_file = start_file or getattr(os, "startfile", None)
        self._dialog_type = dialog_type
        self._window: DialogWindow | None = None
        # One dialog at a time, so a double click can't open two.
        self._dialog_open = threading.Lock()

    def attach(self, window: DialogWindow) -> None:
        """Use this window for dialogs from now on."""
        self._window = window

    def choose(
        self,
        kind: DialogKind,
        *,
        directory: Path | None = None,
        multiple: bool = False,
        file_types: Sequence[str] = (),
    ) -> list[str]:
        """Ask for files or a folder. Returns the paths chosen, or none when it's cancelled.

        Raises ScmpError(internal) before the window is open.
        """
        window = self._window
        if window is None:
            raise ScmpError(ErrorKind.internal, "a file dialog was asked for before the window")
        if not self._dialog_open.acquire(blocking=False):
            log.info("A file dialog is open already, so another one wasn't")
            return []
        try:
            chosen = window.create_file_dialog(
                self._dialog_type(kind),
                directory=str(directory) if directory else "",
                allow_multiple=multiple,
                file_types=tuple(file_types),
            )
        finally:
            self._dialog_open.release()
        return [str(path) for path in chosen or ()]

    def reveal(self, path: Path) -> bool:
        """Show a file selected in the file manager, or its folder if the file has gone.

        Returns False when neither is there.
        """
        is_file = path.is_file()
        folder = path if path.is_dir() else path.parent
        if not is_file and not folder.is_dir():
            return False
        target = path if is_file else folder
        command = REVEAL_COMMANDS.get(self._platform, _linux_reveal)(target, is_file)
        self._spawn(command)
        return True

    def open_link(self, link: str) -> bool:
        """Open a link such as spotify: with the app the OS has for it. False if that failed."""
        if self._platform == "win32" and self._start_file is not None:
            try:
                self._start_file(link)
            except OSError as error:
                log.info("Windows couldn't open a %s link: %s", link.split(":")[0], error)
                return False
            return True
        opener = OPENERS.get(self._platform, "xdg-open")
        try:
            return self._run_opener([opener, link])
        except (OSError, subprocess.TimeoutExpired) as error:
            log.info("%s couldn't open a link: %s", opener, error)
            return False
