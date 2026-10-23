"""queue.json, which keeps the jobs not saved yet so the queue comes back after a restart.

A file whose content can't be used is renamed queue.bad-<time>.json, as a damaged settings.json is,
and the queue starts empty. A file that can't be opened at all is left alone, and nothing is
written over it until the next start (TECH_SPEC §17).
"""

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError

FILE_NAME = "queue.json"
VERSION = 1

log = logging.getLogger(__name__)


def default_path() -> Path:
    """Where queue.json lives."""
    return paths.data_dir() / FILE_NAME


class QueueFile:
    """queue.json on disk: a version number and one entry per job, in queue order."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = datetime.now) -> None:
        self.path = path
        self._clock = clock
        # Set when the file couldn't be read, so a save can't destroy a queue that may be fine.
        self._save_refused = False

    def load(self) -> list[dict[str, Any]]:
        """The entries in the file. A missing or unusable file gives none."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as error:
            self._refuse_saves(error)
            return []
        try:
            return _entries(raw)
        except ValueError as error:
            self.move_aside(str(error))
            return []

    def save(self, entries: list[str]) -> None:
        """Write the entries, each already JSON, atomically. Raises ScmpError(disk)."""
        if self._save_refused:
            raise ScmpError(ErrorKind.disk, "queue.json couldn't be read, so it isn't overwritten")
        # Joined rather than dumped whole, so each entry's JSON can be kept from the last write.
        text = f'{{"version": {VERSION}, "jobs": [{", ".join(entries)}]}}\n'
        paths.write_text_atomically(self.path, text)

    def remove_leftovers(self) -> None:
        """Delete the temp files that writes cut off by a crash left next to queue.json.

        Call it before any write starts, since a write's own temp file has the same shape.
        """
        for leftover in self.path.parent.glob(f".{self.path.name}.*.tmp"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as error:
                log.warning("Couldn't delete a leftover queue.json temp file: %s", error.strerror)

    def move_aside(self, why: str) -> None:
        """Rename the file queue.bad-<time>.json, keeping it for a bug report."""
        backup = paths.backup_path(self.path, self._clock())
        try:
            os.replace(self.path, backup)
        except FileNotFoundError:
            return
        except OSError as error:
            # Without a backup the file is the only copy, so it mustn't be overwritten either.
            self._refuse_saves(error)
            return
        log.warning("queue.json couldn't be used (%s), so it was renamed %s", why, backup.name)

    def _refuse_saves(self, error: OSError) -> None:
        self._save_refused = True
        log.warning(
            "queue.json couldn't be opened (%s), so the queue isn't kept until a restart",
            error.strerror or error,
        )
        log.debug("Reading %s failed", self.path, exc_info=error)


def _entries(raw: bytes) -> list[dict[str, Any]]:
    """The job entries in the file's bytes. Raises ValueError when they can't be used."""
    # utf-8-sig also accepts the byte order mark some Windows editors add.
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError("it isn't a version 1 queue")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(entry, dict) for entry in jobs):
        raise ValueError("its jobs aren't a list of objects")
    return jobs
