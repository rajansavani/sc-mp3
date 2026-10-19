"""An exclusive lock on a file, so two sc-mp3 processes don't do the same thing at once.

It uses msvcrt.locking on Windows and fcntl.flock elsewhere. Closing the file, or the process
ending, releases the lock.
"""

import errno
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

from scmp3.core.errors import ErrorKind, ScmpError

RETRY_EVERY = 0.1  # seconds

Clock = Callable[[], float]
Sleep = Callable[[float], None]

if sys.platform == "win32":
    import msvcrt

    # What msvcrt.locking raises when another handle holds the byte.
    _BUSY = {errno.EACCES, errno.EDEADLOCK}

    def _try_lock(handle: BinaryIO) -> bool:
        # msvcrt locks bytes from the current position, so every lock is on the first byte.
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in _BUSY:
                return False
            raise
        return True

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LockTimeout(TimeoutError):
    """Another process held the lock for longer than the timeout."""


class LockUnavailable(ScmpError):
    """The lock file can't be opened or locked here, as on an NFS home with no lock service."""

    def __init__(self, detail: str) -> None:
        super().__init__(ErrorKind.disk, detail)


class FileLock:
    """An exclusive lock on `path`, held by one open handle at a time across all processes.

    `acquire` tries again every 0.1 seconds until `timeout` seconds pass, and a timeout of 0
    tries once. Use it as a context manager, or call acquire and release.
    """

    def __init__(
        self,
        path: Path,
        timeout: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._path = path
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Take the lock, or raise LockTimeout when it isn't free in time.

        Raises LockUnavailable when the file can't be opened or the system refuses to lock it.
        """
        handle = self._open()
        deadline = self._clock() + self._timeout
        while not _try_lock_or_close(handle):
            if self._clock() >= deadline:
                handle.close()
                raise LockTimeout(f"{self._path.name} is locked by another process")
            self._sleep(RETRY_EVERY)
        self._handle = handle

    def release(self) -> None:
        """Let go of the lock. The file stays, for the next process to lock."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
        finally:
            handle.close()

    def _open(self) -> BinaryIO:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode creates the file without emptying it, since another process may hold it.
            return open(self._path, "a+b")
        except OSError as error:
            raise LockUnavailable(
                f"can't open {self._path.name}: {error.strerror or type(error).__name__}"
            ) from error

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _try_lock_or_close(handle: BinaryIO) -> bool:
    """Try the lock once. Any failure but "busy" closes the handle and raises LockUnavailable."""
    try:
        return _try_lock(handle)
    except OSError as error:
        handle.close()
        # flock gives ENOLCK, for example, on an NFS home folder with no lock service.
        raise LockUnavailable(
            f"file locks don't work here: {error.strerror or type(error).__name__}"
        ) from error
