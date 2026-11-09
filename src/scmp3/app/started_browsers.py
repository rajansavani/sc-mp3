"""The browsers one sign-in starts, so sc-mp3 can stop them if it closes before the sign-in ends.

Once closed, it starts no more browsers, and the profile it was told about stays as it was, so the
sign-in's own thread can't slip a browser or a change past sc-mp3 closing.
"""

import subprocess
import threading
from pathlib import Path

from scmp3.app import chromium
from scmp3.core.errors import ErrorKind, ScmpError


class StartedBrowsers:
    """The browsers one sign-in started, and the profile a read may not have cleared yet."""

    def __init__(self) -> None:
        # Held while a browser starts, so closing can't come between its start and its record.
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen[bytes]] = []
        self._unread: Path | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def unread(self) -> Path | None:
        """The profile that may hold a SoundCloud session no read has deleted yet."""
        with self._lock:
            return self._unread

    def start(
        self, browser: Path, profile: Path, url: str, flags: tuple[str, ...]
    ) -> subprocess.Popen[bytes]:
        """Start a browser for this sign-in. Raises internal once sc-mp3 is closing."""
        with self._lock:
            if self._closed:
                raise ScmpError(ErrorKind.internal, "sc-mp3 is closing, so no browser starts")
            process = chromium.start(browser, profile, url, flags)
            self._processes.append(process)
            return process

    def reading(self, profile: Path | None) -> None:
        """Note the profile a read starts on, or None once its SoundCloud session is dealt with."""
        with self._lock:
            if not self._closed:
                self._unread = profile

    def close(self) -> tuple[list[subprocess.Popen[bytes]], Path | None]:
        """Start nothing more. Returns the browsers still running, and the profile left unread."""
        with self._lock:
            self._closed = True
            running = [process for process in self._processes if process.poll() is None]
            return running, self._unread
