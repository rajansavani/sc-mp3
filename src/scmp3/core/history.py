"""Saved files, kept in sqlite so the app can warn before saving the same track twice."""

import logging
import os
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import HistoryEntry
from scmp3.core.placing import BRIEF_LOCK_ERRORS, LOCK_RETRY_DELAYS

DB_NAME = "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    url_norm TEXT,
    saved_path TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    format TEXT NOT NULL,
    bitrate INTEGER,
    saved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_source_id ON history (source_id);
CREATE INDEX IF NOT EXISTS history_url_norm ON history (url_norm);
"""

# The columns an insert fills. The database assigns the id.
INSERT_COLUMNS = (
    "source_id",
    "url_norm",
    "saved_path",
    "title",
    "artist",
    "album",
    "format",
    "bitrate",
    "saved_at",
)
COLUMNS = ", ".join(("id", *INSERT_COLUMNS))
INSERT = (
    f"INSERT INTO history ({', '.join(INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})"
)
NEWEST_FIRST = "ORDER BY saved_at DESC, id DESC"

# Each search word has to appear in one of these, ignoring case.
SEARCH_FIELDS = ("title", "artist", "album")
# Words past this are ignored, since every word adds clauses and sqlite caps a query's depth.
MAX_SEARCH_WORDS = 10

# The sqlite errors that mean the file isn't a usable database, rather than a passing problem.
DAMAGED = ("SQLITE_NOTADB", "SQLITE_CORRUPT")

log = logging.getLogger(__name__)


def default_path() -> Path:
    """Where history.db lives."""
    return paths.data_dir() / DB_NAME


def _now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


class History:
    """The saved-files store. Each call opens its own connection, so any thread can use it."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self._clock = clock
        self._sleep = sleep
        # Held while a connection is opened, and while a damaged file is replaced. So no other
        # thread has the damaged file open when it's renamed, which Windows would refuse.
        self._open_lock = threading.Lock()
        # Set once this run has checked the tables, so later connections skip the schema script.
        self._schema_ready = False

    def add(self, entry: HistoryEntry) -> HistoryEntry:
        """Record a saved file. Returns the entry with its id and save time filled in."""
        saved_at = (entry.saved_at or self._clock()).astimezone(UTC)
        values = (
            entry.source_id,
            entry.url_norm,
            str(entry.saved_path),
            entry.title,
            entry.artist,
            entry.album,
            entry.format,
            entry.bitrate,
            # A fixed width keeps text order the same as time order.
            saved_at.isoformat(timespec="microseconds"),
        )
        with self._connect() as db:
            cursor = db.execute(INSERT, values)
        return replace(entry, id=cursor.lastrowid, saved_at=saved_at)

    def find_by_source_id(self, source_id: str) -> HistoryEntry | None:
        """The latest save of this source, such as soundcloud:tracks:123."""
        return self._first("source_id = ?", source_id)

    def find_by_url(self, url_norm: str) -> HistoryEntry | None:
        """The latest save of this normalized URL."""
        return self._first("url_norm = ?", url_norm)

    def recent(self, limit: int = 50) -> list[HistoryEntry]:
        """The latest saves, newest first."""
        with self._connect() as db:
            rows = db.execute(f"SELECT {COLUMNS} FROM history {NEWEST_FIRST} LIMIT ?", (limit,))
            return [_entry(row) for row in rows]

    def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        """Saves whose title, artist or album contain every word of `query`, newest first."""
        words = _fold(query).split()[:MAX_SEARCH_WORDS]
        if not words:
            return self.recent(limit)
        # One clause per word: the word appears in at least one search field.
        one_word = " OR ".join(f"instr(fold({name}), ?) > 0" for name in SEARCH_FIELDS)
        where = " AND ".join(f"({one_word})" for _ in words)
        params = [word for word in words for _ in SEARCH_FIELDS]
        with self._connect() as db:
            sql = f"SELECT {COLUMNS} FROM history WHERE {where} {NEWEST_FIRST} LIMIT ?"
            return [_entry(row) for row in db.execute(sql, (*params, limit))]

    def _first(self, condition: str, value: str) -> HistoryEntry | None:
        """The newest entry matching a one-parameter condition."""
        with self._connect() as db:
            sql = f"SELECT {COLUMNS} FROM history WHERE {condition} {NEWEST_FIRST} LIMIT 1"
            row = db.execute(sql, (value,)).fetchone()
        return _entry(row) if row else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open the database in a transaction, creating it if needed, and close it afterwards."""
        db = self._open()
        try:
            with db:
                yield db
        except sqlite3.Error as error:
            if isinstance(error, sqlite3.DatabaseError) and _is_damaged(error):
                # The next connection checks the file again, and replaces it if it's damaged.
                with self._open_lock:
                    self._schema_ready = False
            raise ScmpError(ErrorKind.disk, f"history database error: {error}") from error
        finally:
            db.close()

    def _open(self) -> sqlite3.Connection:
        """Connect with the schema in place. A damaged file is moved aside and a new one started."""
        with self._open_lock:
            db = self._try_open()
            if db:
                return db
            # The failed attempt has closed its connection, and the lock keeps others out.
            self._move_aside()
            db = self._try_open()
        if db is None:
            raise ScmpError(ErrorKind.disk, "a new history database couldn't be started")
        return db

    def _try_open(self) -> sqlite3.Connection | None:
        """Connect, or return None when the file is damaged. Other failures raise ScmpError."""
        try:
            return self._open_once()
        except sqlite3.DatabaseError as error:
            if not _is_damaged(error):
                raise ScmpError(
                    ErrorKind.disk, f"can't open the history database: {error}"
                ) from error
            log.debug("history.db is damaged: %s", error)
            return None

    def _open_once(self) -> sqlite3.Connection:
        """Connect, creating the schema if it's missing. Raises sqlite's own errors.

        The schema script runs on this run's first connection and for a file that has gone, since
        it's most of what opening costs (T-022b). It's also what finds a damaged file.
        """
        needs_schema = not self._schema_ready or not self.path.exists()
        if needs_schema:
            self._create_folder()
        db = sqlite3.connect(self.path)
        try:
            db.row_factory = sqlite3.Row
            # sqlite's own lower() only folds ASCII, so searches fold text in Python.
            db.create_function("fold", 1, _fold_value, deterministic=True)
            if needs_schema:
                db.executescript(SCHEMA)
        except BaseException:
            db.close()
            raise
        self._schema_ready = True
        return db

    def _create_folder(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ScmpError(
                ErrorKind.disk, f"can't create the data folder: {error.strerror or error}"
            ) from error

    def _move_aside(self) -> None:
        """Rename a damaged history.db to history.bad-<time>.db, in local time."""
        backup = paths.backup_path(self.path, self._clock().astimezone())
        try:
            self._rename(backup)
        except OSError as error:
            raise ScmpError(
                ErrorKind.disk,
                f"history.db is damaged and couldn't be moved aside: {error.strerror or error}",
            ) from error
        log.warning(
            "history.db wasn't a usable database, so it was renamed %s and a new one started",
            backup.name,
        )

    def _rename(self, backup: Path) -> None:
        """os.replace, tried again for about 2 seconds while Windows says the file is locked."""
        # Another program, such as Defender or a backup tool, can still have it open briefly.
        for delay in LOCK_RETRY_DELAYS:
            try:
                os.replace(self.path, backup)
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in BRIEF_LOCK_ERRORS:
                    raise
            log.debug("history.db is locked, so the rename is tried again")
            self._sleep(delay)
        os.replace(self.path, backup)


def _is_damaged(error: sqlite3.DatabaseError) -> bool:
    """Whether sqlite says the file isn't a database or is corrupt."""
    name = getattr(error, "sqlite_errorname", "")
    return name.startswith(DAMAGED)


def _fold(text: str) -> str:
    """Text for comparing without case, with accents composed the same way (NFC)."""
    return unicodedata.normalize("NFC", text.casefold())


def _fold_value(value: object) -> object:
    """`_fold` for a column value. NULL and other non-text values pass through."""
    return _fold(value) if isinstance(value, str) else value


def _entry(row: sqlite3.Row) -> HistoryEntry:
    """Turn a database row back into an entry."""
    return HistoryEntry(
        id=row["id"],
        source_id=row["source_id"],
        url_norm=row["url_norm"],
        saved_path=Path(row["saved_path"]),
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        format=row["format"],
        bitrate=row["bitrate"],
        saved_at=datetime.fromisoformat(row["saved_at"]),
    )
