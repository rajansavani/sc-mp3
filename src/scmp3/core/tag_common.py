"""What both tag formats share: turning mutagen's errors into ScmpError, and joining values."""

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from mutagen import MutagenError

from scmp3.core.errors import ErrorKind, ScmpError

log = logging.getLogger(__name__)


@contextmanager
def file_errors(action: str) -> Iterator[None]:
    """Turn mutagen's errors into ScmpError: disk for file system errors, else convert_failed."""
    try:
        yield
    # mutagen raises a bare ValueError when a damaged tag claims to be bigger than the file.
    except (MutagenError, OSError, ValueError) as error:
        # mutagen's messages often hold the file's full path, which belongs in debug logs only.
        log.debug("Couldn't %s: %s", action, error)
        os_error = _os_error(error)
        if os_error is not None:
            reason = os_error.strerror or type(os_error).__name__
            raise ScmpError(ErrorKind.disk, f"couldn't {action}: {reason}") from error
        detail = f"couldn't {action}: {type(error).__name__}"
        raise ScmpError(ErrorKind.convert_failed, detail) from error


def _os_error(error: Exception) -> OSError | None:
    """The file system error behind `error`. mutagen raises its own while handling the original.

    An OSError without an errno doesn't count: mutagen raises one for a file cut short.
    """
    for candidate in (error, error.__context__):
        if isinstance(candidate, OSError) and candidate.errno is not None:
            return candidate
    return None


def joined(values: Iterable[object]) -> str | None:
    """One string from a frame's or atom's values, since other taggers can store several."""
    text = ", ".join(str(value) for value in values if str(value))
    return text or None
