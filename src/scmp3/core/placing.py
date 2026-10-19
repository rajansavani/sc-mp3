"""Moving finished files into the library and re-tagging them there, safely.

A file only ever appears in the library complete and under its final name.
"""

import errno
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import PARTIAL_SUFFIX, RETAG_SUFFIX

log = logging.getLogger(__name__)

Sleep = Callable[[float], None]
Clock = Callable[[], float]

# Windows' version of EXDEV, for a rename from one drive to another.
ERROR_NOT_SAME_DEVICE = 17
# Access denied and sharing violation: what Windows says while Defender, the search indexer
# or OneDrive has a file open for a moment.
BRIEF_LOCK_ERRORS = frozenset({5, 32})
# The waits between tries of a locked rename, about 2 seconds in all.
LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.6, 0.7)

# A temp name this module makes: a dot, a saved file's name with its extension, the suffix.
LEFTOVER_NAME = re.compile(r"\.[^.].*\.[0-9A-Za-z]{1,5}(?:\.partial|\.retag\.tmp)")
# Older than this, a temp file can't belong to a save that's still running.
LEFTOVER_AGE = 10 * 60  # seconds


def atomic_place(src: Path, dest: Path, *, sleep: Sleep = time.sleep) -> Path:
    """Move `src` to `dest` in one step, so `dest` never holds a half-written file.

    A file already at `dest` is replaced, so resolve collisions first. On the same drive `src`
    is renamed. From another drive it's copied into a hidden .partial file next to `dest`,
    swapped in, then deleted. If anything fails, `src` and any file at `dest` stay as they were.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not _rename_on_same_drive(src, dest, sleep):
            _copy_from_another_drive(src, dest, sleep)
    except OSError as error:
        raise _disk_error(dest, error) from error
    log.debug("Placed %s", dest)
    return dest


def _rename_on_same_drive(src: Path, dest: Path, sleep: Sleep) -> bool:
    """Rename `src` to `dest`. False, with nothing changed, when they're on different drives."""
    _flush_to_disk(src)
    try:
        _replace(src, dest, sleep)
        return True
    except OSError as error:
        if _is_other_drive(error):
            return False
        raise


def _copy_from_another_drive(src: Path, dest: Path, sleep: Sleep) -> None:
    """Copy `src` into a hidden .partial file next to `dest`, swap it in, then delete `src`."""
    log.debug("%s is on another drive, so it's copied", src)
    partial = _hidden_sibling(dest, PARTIAL_SUFFIX)
    try:
        shutil.copyfile(src, partial)
        _flush_to_disk(partial)
        _replace(partial, dest, sleep)
    finally:
        # After a successful replace the partial name is gone, so this only cleans up failures.
        _remove_or_warn(partial)
    # The copy is in place, so the original on the other drive can go.
    _remove_or_warn(src)


def _is_other_drive(error: OSError) -> bool:
    """Whether a rename failed only because the two paths are on different drives."""
    if error.errno == errno.EXDEV:
        return True
    return getattr(error, "winerror", None) == ERROR_NOT_SAME_DEVICE


def _replace(src: Path, dest: Path, sleep: Sleep) -> None:
    """os.replace, tried again for about 2 seconds while Windows says a file is briefly locked."""
    for delay in LOCK_RETRY_DELAYS:
        try:
            os.replace(src, dest)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in BRIEF_LOCK_ERRORS:
                raise
        log.debug("%s is locked, so the rename is tried again", dest.name)
        sleep(delay)
    os.replace(src, dest)


def retag_in_place(
    path: Path, write_fn: Callable[[Path], None], *, sleep: Sleep = time.sleep
) -> None:
    """Write tags to a copy of `path` next to it, then swap the copy in.

    `write_fn` gets the copy's path, which ends in .retag.tmp rather than the audio extension.
    If anything fails, `path` is untouched and the copy is deleted.
    """
    temp = _hidden_sibling(path, RETAG_SUFFIX)
    try:
        shutil.copyfile(path, temp)
        write_fn(temp)
        _flush_to_disk(temp)
        # After the flush, since a read-only copy can't be opened for writing.
        _copy_permissions(path, temp)
        # The swapped-in file has a new modification time, which helps Spotify notice it.
        _replace(temp, path, sleep)
    except OSError as error:
        raise _disk_error(path, error) from error
    finally:
        # After a successful replace the temp name is gone, so this only cleans up failures.
        _remove_or_warn(temp)
    log.debug("Re-tagged %s", path)


def _copy_permissions(original: Path, copy: Path) -> None:
    # Windows has only a read-only flag, and it can't replace a read-only file anyway.
    if sys.platform != "win32":
        shutil.copymode(original, copy)


def remove_leftovers(library_dir: Path, *, now: Clock = time.time) -> list[Path]:
    """Delete temp files that a crash left in the library folder or its album folders.

    Only names this module makes count, and only when they're over 10 minutes old, so the
    saves of another running copy are safe. Returns the files deleted.
    """
    cutoff = now() - LEFTOVER_AGE
    removed: list[Path] = []
    for path in _temp_files_under(library_dir):
        if _modified_before(path, cutoff) and _remove_or_warn(path):
            log.debug("Deleted the leftover %s", path)
            removed.append(path)
    return removed


def _temp_files_under(folder: Path) -> Iterator[Path]:
    """Every file under `folder` named like one of this module's temp files."""
    # Before the first save there's no library folder yet.
    if not folder.is_dir():
        return
    for root, _folders, files in os.walk(folder, onerror=_warn_unreadable_folder):
        for name in files:
            if LEFTOVER_NAME.fullmatch(name):
                yield Path(root, name)


def _warn_unreadable_folder(error: OSError) -> None:
    log.warning("Couldn't look for leftover files in a folder: %s", error.strerror or error)


def _modified_before(path: Path, cutoff: float) -> bool:
    try:
        return path.lstat().st_mtime < cutoff
    except OSError as error:
        # Another running copy may have just finished with it.
        log.debug("Skipped %s: %s", path, error.strerror or error)
        return False


def _hidden_sibling(path: Path, suffix: str) -> Path:
    """A temp name next to `path`, such as .Song.mp3.partial."""
    # The leading dot hides it on macOS and Linux.
    return path.with_name(f".{path.name}{suffix}")


def _flush_to_disk(path: Path) -> None:
    """Make sure the file's bytes are on disk before it's swapped in."""
    # Windows can only flush a handle that's open for writing.
    with path.open("rb+") as file:
        os.fsync(file.fileno())


def _remove_or_warn(path: Path) -> bool:
    """Delete `path` if it's there, logging a failure instead of raising it. True if it's gone."""
    # This often runs while another error is on its way up, which a new one would hide.
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as error:
        log.warning("Couldn't delete %s: %s", path.name, error.strerror or error)
        return False


def _disk_error(path: Path, error: OSError) -> ScmpError:
    # The detail names the file only; full paths stay in DEBUG logs.
    return ScmpError(ErrorKind.disk, f"can't write {path.name}: {error.strerror or error}")
