"""Re-tagging a file where it is: a tagged copy swapped in, or written through for a file with
other hard links. If anything fails, the file is as it was."""

import ctypes
import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from scmp3.core import placing
from scmp3.core.library import RETAG_SUFFIX
from scmp3.core.placing import (
    FileInUse,
    Sleep,
    disk_error,
    flush_to_disk,
    hidden_sibling,
    is_brief_lock,
    remove_or_warn,
    replace_retrying,
)

log = logging.getLogger(__name__)

# The backup of a hard-linked file rewritten in place. remove_leftovers never deletes one, since
# after a crash it may be the only good copy.
BACKUP_SUFFIX = ".retag.bak"


def retag_in_place(
    path: Path, write_fn: Callable[[Path], None], *, sleep: Sleep = time.sleep
) -> None:
    """Write tags to a copy of `path` next to it, then swap the copy in.

    `write_fn` gets the copy's path, which ends in .retag.tmp rather than the audio extension.
    If anything fails, `path` is untouched and the copy is deleted. A symlink's target is the
    file re-tagged. A file with other hard links is written in place instead, so every name sees
    the new tags, with a backup that's put back if the write fails.
    """
    # Swapping a copy in where a link was would leave the real file with its old tags.
    real = Path(os.path.realpath(path)) if path.is_symlink() else path
    temp = hidden_sibling(real, RETAG_SUFFIX)
    try:
        _copy_for_retag(real, temp)
        write_fn(temp)
        flush_to_disk(temp)
        if _has_other_names(real):
            _write_through(real, temp, sleep)
        else:
            _swap_in(real, temp, sleep)
    except OSError as error:
        raise disk_error(real, error) from error
    finally:
        # After a successful swap the temp name is gone, so this only cleans up failures.
        remove_or_warn(temp)
    log.debug("Re-tagged %s", real)


def _copy_for_retag(path: Path, temp: Path) -> None:
    """Copy the file to tag. Reading it refused means another program holds it with no sharing."""
    try:
        shutil.copyfile(path, temp)
    except PermissionError as error:
        # A copy refused for the folder, rather than the file, stays a plain disk error.
        if _readable(path):
            raise
        raise FileInUse(error.errno, error.strerror) from error


def _readable(path: Path) -> bool:
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def _swap_in(path: Path, temp: Path, sleep: Sleep) -> None:
    """Give the tagged copy the file's permissions and attributes, then rename it over the file."""
    # After the flush, since a read-only copy can't be opened for writing.
    _copy_permissions(path, temp)
    # The swapped-in file has a new modification time, which helps Spotify notice it.
    replace_retrying(temp, path, sleep)


def _has_other_names(path: Path) -> bool:
    """Whether the file has other hard links, which a swap would leave on the old file."""
    return os.stat(path).st_nlink > 1


def _write_through(path: Path, temp: Path, sleep: Sleep) -> None:
    """Copy the tagged bytes into the file itself, keeping its other names. A backup next to it is
    put back if the copy fails partway, and kept when even that fails, so nothing is lost."""
    backup = hidden_sibling(path, BACKUP_SUFFIX)
    shutil.copyfile(path, backup)
    flush_to_disk(backup)
    try:
        file = _open_for_writing(path, sleep)
    except OSError:
        # Nothing was written, so the backup isn't needed.
        remove_or_warn(backup)
        raise
    with file:
        try:
            _copy_into(temp, file)
        except OSError:
            _put_back(backup, file)
            raise
    remove_or_warn(backup)


def _open_for_writing(path: Path, sleep: Sleep) -> BinaryIO:
    """Open the file to rewrite, tried again for about 2 seconds while Windows says it's locked."""
    for delay in placing.LOCK_RETRY_DELAYS:
        try:
            return path.open("r+b")
        except OSError as error:
            if not is_brief_lock(error):
                raise
        sleep(delay)
    try:
        return path.open("r+b")
    except OSError as error:
        if is_brief_lock(error):
            raise FileInUse(error.errno, error.strerror) from error
        raise


def _copy_into(source: Path, file: BinaryIO) -> None:
    """Make an open file hold exactly the bytes of `source`, and flush them to disk."""
    file.seek(0)
    with source.open("rb") as reader:
        shutil.copyfileobj(reader, file)
    file.truncate()
    file.flush()
    os.fsync(file.fileno())


def _put_back(backup: Path, file: BinaryIO) -> None:
    """Restore the file from its backup after a failed write. The backup stays if this fails."""
    try:
        _copy_into(backup, file)
    except OSError as error:
        log.error("Couldn't put %s back from %s: %s", file.name, backup.name, error)
        return
    remove_or_warn(backup)


def _copy_permissions(original: Path, copy: Path) -> None:
    """Give the copy the original's mode, or on Windows its hidden and other attributes."""
    if sys.platform == "win32":
        _copy_windows_attributes(original, copy)
        return
    shutil.copymode(original, copy)


# Windows attributes a swapped-in copy keeps. Read-only isn't one, since Windows can't replace a
# read-only file anyway.
KEPT_WINDOWS_ATTRIBUTES = (
    stat.FILE_ATTRIBUTE_HIDDEN
    | stat.FILE_ATTRIBUTE_SYSTEM
    | stat.FILE_ATTRIBUTE_ARCHIVE
    | stat.FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
)


def _copy_windows_attributes(original: Path, copy: Path) -> None:
    attributes = os.stat(original).st_file_attributes & KEPT_WINDOWS_ATTRIBUTES
    if not attributes:
        return
    set_attributes = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
    set_attributes.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
    set_attributes.restype = ctypes.c_int
    if not set_attributes(str(copy), attributes):
        raise ctypes.WinError(ctypes.get_last_error())
