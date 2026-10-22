"""The clipboard offer: reading the system clipboard, and picking the link worth offering.

WebView2 asks the user before a page reads the clipboard outside a paste, and WebKit never lets
it, so Python reads it instead (TECH_SPEC §16). The text is never logged.
"""

import ctypes
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from typing import Any

from scmp3.core import pipeline
from scmp3.core.inputs import parse_inputs

# Reads the clipboard's text, or None when it holds none or can't be read.
ClipboardReader = Callable[[], str | None]
# Runs a command and returns what it printed, or raises OSError or SubprocessError.
CommandRunner = Callable[[Sequence[str]], bytes]

# Only the start of a long clipboard is read.
MAX_CHARACTERS = 100_000
# How long a clipboard command on macOS or Linux may take.
COMMAND_TIMEOUT = 2.0

# Windows' clipboard format for text, and how often to try opening a clipboard another app holds.
CF_UNICODETEXT = 13
OPEN_TRIES = 5
OPEN_PAUSE = 0.02  # seconds

# The commands that print the clipboard on Linux, in the order they're tried.
LINUX_COMMANDS = (
    ("wl-paste", "--no-newline"),
    ("xclip", "-selection", "clipboard", "-o"),
    ("xsel", "--clipboard", "--output"),
)

log = logging.getLogger(__name__)


def offered_link(text: str, is_queued: Callable[[str], bool]) -> str | None:
    """The first link in the text that sc-mp3 can save and hasn't queued, normalized.

    The text is read the way adding reads it, so the offer and adding agree.
    """
    for parsed in parse_inputs(text[:MAX_CHARACTERS]):
        if parsed.url is None or pipeline.unsupported_reason(parsed) is not None:
            continue
        if not is_queued(parsed.url):
            return parsed.url
    return None


def _run_command(command: Sequence[str]) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=COMMAND_TIMEOUT, check=True)
    return result.stdout


def _decoded(output: bytes) -> str:
    return output[: MAX_CHARACTERS * 4].decode("utf-8", errors="replace")


def read_mac(run: CommandRunner = _run_command) -> str | None:
    """The clipboard's text on macOS, from pbpaste."""
    return _decoded(run(["pbpaste"]))


def read_linux(
    run: CommandRunner = _run_command, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """The clipboard's text on Linux, from the first clipboard tool that's installed."""
    for command in LINUX_COMMANDS:
        if which(command[0]):
            return _decoded(run(command))
    log.debug("No clipboard tool is installed, so the clipboard offer stays quiet")
    return None


class WindowsClipboard:
    """The Win32 calls that read text from the clipboard, with their types set."""

    def __init__(self) -> None:
        # Private copies, so setting the argument types changes nothing shared.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.open = _typed(user32.OpenClipboard, [wintypes.HWND], wintypes.BOOL)
        self.close = _typed(user32.CloseClipboard, [], wintypes.BOOL)
        self.has_format = _typed(user32.IsClipboardFormatAvailable, [wintypes.UINT], wintypes.BOOL)
        self.get_data = _typed(user32.GetClipboardData, [wintypes.UINT], wintypes.HANDLE)
        self.lock = _typed(kernel32.GlobalLock, [wintypes.HGLOBAL], wintypes.LPVOID)
        self.unlock = _typed(kernel32.GlobalUnlock, [wintypes.HGLOBAL], wintypes.BOOL)
        self.size = _typed(kernel32.GlobalSize, [wintypes.HGLOBAL], ctypes.c_size_t)


def _typed(function: Any, argtypes: list[Any], restype: Any) -> Any:
    function.argtypes = argtypes
    function.restype = restype
    return function


def read_windows(
    calls: WindowsClipboard | None = None, sleep: Callable[[float], None] = time.sleep
) -> str | None:
    """The clipboard's text on Windows, or None when it holds no text or another app has it."""
    calls = calls or WindowsClipboard()
    if not calls.has_format(CF_UNICODETEXT) or not _open(calls, sleep):
        return None
    try:
        handle = calls.get_data(CF_UNICODETEXT)
        if not handle:
            return None
        return _locked_text(calls, handle)
    finally:
        calls.close()


def _open(calls: WindowsClipboard, sleep: Callable[[float], None]) -> bool:
    """Open the clipboard, trying again for a moment while another app has it open."""
    for _ in range(OPEN_TRIES):
        if calls.open(None):
            return True
        sleep(OPEN_PAUSE)
    log.debug("Another app kept the clipboard open, so it wasn't read")
    return False


def _locked_text(calls: WindowsClipboard, handle: int) -> str | None:
    pointer = calls.lock(handle)
    if not pointer:
        return None
    try:
        # The size is in bytes, two per UTF-16 unit, and the text ends at its first NUL.
        units = min(calls.size(handle) // 2, MAX_CHARACTERS)
        # Decoded as UTF-16 rather than as wchar_t, which is 4 bytes off Windows.
        text = ctypes.string_at(pointer, units * 2).decode("utf-16-le", errors="replace")
        return text.split("\0", 1)[0]
    finally:
        calls.unlock(handle)


# How each OS reads its clipboard. Any other OS reads it the Linux way.
READERS: dict[str, ClipboardReader] = {"win32": read_windows, "darwin": read_mac}


def read_clipboard() -> str | None:
    """The clipboard's text, or None when there's none or it can't be read."""
    reader = READERS.get(sys.platform, read_linux)
    try:
        return reader()
    except (OSError, subprocess.SubprocessError) as error:
        # Only the kind of failure, since the clipboard can hold anything.
        log.debug("The clipboard couldn't be read: %s", type(error).__name__)
        return None
