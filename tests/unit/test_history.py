"""Tests for the saved-files history in sqlite."""

import itertools
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scmp3.core import history, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import HistoryEntry

START = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


class StepClock:
    """A clock that moves one minute forward every time it's read, from any thread."""

    def __init__(self) -> None:
        self._minutes = itertools.count()

    def __call__(self) -> datetime:
        return START + timedelta(minutes=next(self._minutes))


def entry(
    title: str = "Midnight Drive",
    artist: str = "Kaia",
    album: str = "Midnight Drive",
    source_id: str | None = "soundcloud:tracks:1",
    url_norm: str | None = "https://soundcloud.com/kaia/midnight-drive",
) -> HistoryEntry:
    return HistoryEntry(
        source_id=source_id,
        url_norm=url_norm,
        saved_path=Path("Music") / "sc-mp3" / f"{artist} - {title}.mp3",
        title=title,
        artist=artist,
        album=album,
        format="mp3",
        bitrate=320,
    )


@pytest.fixture
def store(tmp_path: Path) -> History:
    return History(tmp_path / "history.db", clock=StepClock())


def test_add_fills_in_the_id_and_the_save_time(store: History) -> None:
    added = store.add(entry())
    assert added.id is not None
    assert added == replace(entry(), id=added.id, saved_at=START)


def test_add_keeps_a_given_save_time_in_utc(store: History) -> None:
    eastern = timezone(timedelta(hours=-4))
    given = datetime(2026, 10, 3, 8, 0, tzinfo=eastern)
    added = store.add(replace(entry(), saved_at=given))
    assert added.saved_at == given
    assert added.saved_at is not None
    assert added.saved_at.tzinfo == UTC


def test_find_by_source_id_returns_the_latest_save(store: History) -> None:
    store.add(entry(title="First"))
    latest = store.add(entry(title="Second"))
    store.add(entry(title="Other", source_id="soundcloud:tracks:2"))
    assert store.find_by_source_id("soundcloud:tracks:1") == latest


def test_find_by_url_returns_the_latest_save(store: History) -> None:
    store.add(entry(title="First", source_id=None))
    latest = store.add(entry(title="Second", source_id=None))
    assert store.find_by_url("https://soundcloud.com/kaia/midnight-drive") == latest


def test_find_returns_none_when_nothing_matches(store: History) -> None:
    store.add(entry())
    assert store.find_by_source_id("soundcloud:tracks:999") is None
    assert store.find_by_url("https://soundcloud.com/someone/else") is None


def test_an_empty_history_finds_nothing(store: History) -> None:
    assert store.find_by_source_id("soundcloud:tracks:1") is None
    assert store.recent() == []
    assert store.search("kaia") == []


def test_recent_lists_the_newest_first_up_to_the_limit(store: History) -> None:
    added = [store.add(entry(title=f"Track {n}")) for n in range(5)]
    assert store.recent(limit=3) == [added[4], added[3], added[2]]


def test_entries_read_back_with_their_types(store: History) -> None:
    store.add(entry())
    [found] = store.recent()
    assert isinstance(found.saved_path, Path)
    assert found.saved_path == entry().saved_path
    assert found.saved_at == START
    assert found.bitrate == 320


def test_search_matches_title_artist_or_album_ignoring_case(store: History) -> None:
    drive = store.add(entry(title="Midnight Drive", artist="Kaia", album="Night"))
    bus = store.add(entry(title="Night Bus", artist="Teo Marr", album="Commute"))
    lantern = store.add(entry(title="Glow", artist="Mara Lune", album="Lantern"))
    assert store.search("NIGHT") == [bus, drive]
    assert store.search("teo") == [bus]
    assert store.search("lantern") == [lantern]


def test_search_needs_every_word_somewhere(store: History) -> None:
    drive = store.add(entry(title="Midnight Drive", artist="Kaia"))
    store.add(entry(title="Midnight Snack", artist="Juno Vale"))
    assert store.search("kaia midnight") == [drive]


def test_search_folds_case_beyond_ascii(store: History) -> None:
    found = store.add(entry(title="ÉTÉ", artist="Straße"))
    assert store.search("été") == [found]
    assert store.search("STRASSE") == [found]


def test_search_treats_sql_wildcards_as_plain_text(store: History) -> None:
    store.add(entry(title="Plain"))
    percent = store.add(entry(title="100% Pure"))
    assert store.search("%") == [percent]
    assert store.search("_") == []


def test_an_empty_search_lists_the_recent_saves(store: History) -> None:
    added = [store.add(entry(title=f"Track {n}")) for n in range(3)]
    assert store.search("  ", limit=2) == [added[2], added[1]]


def test_history_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    added = History(db, clock=StepClock()).add(entry())
    assert History(db).find_by_source_id("soundcloud:tracks:1") == added


def test_history_creates_its_folder(tmp_path: Path) -> None:
    db = tmp_path / "new" / "folder" / "history.db"
    History(db, clock=StepClock()).add(entry())
    assert db.exists()


def test_threads_can_add_at_the_same_time(store: History) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.add(entry(title=f"Track {n}")), range(20)))
    assert len(store.recent(limit=100)) == 20


def test_an_unopenable_database_is_a_disk_error(tmp_path: Path) -> None:
    folder_in_the_way = tmp_path / "history.db"
    folder_in_the_way.mkdir()
    with pytest.raises(ScmpError) as error_info:
        History(folder_in_the_way).recent()
    assert error_info.value.kind is ErrorKind.disk


def test_the_default_path_is_in_the_data_folder(scmp3_home: Path) -> None:
    assert history.default_path() == paths.data_dir() / "history.db"


def test_search_finds_decomposed_accents_with_a_composed_query(store: History) -> None:
    decomposed = store.add(entry(title="Été"))
    assert store.search("été") == [decomposed]
    assert store.search("ÉTÉ") == [decomposed]


def test_a_very_long_search_uses_only_its_first_words(store: History) -> None:
    drive = store.add(entry(title="Midnight Drive", artist="Kaia"))
    query = "kaia " + " ".join(["midnight"] * 1000)
    assert store.search(query) == [drive]


def test_words_past_the_limit_are_ignored(store: History) -> None:
    drive = store.add(entry(title="Midnight Drive", artist="Kaia"))
    query = " ".join(["kaia"] * history.MAX_SEARCH_WORDS + ["nowhere"])
    assert store.search(query) == [drive]


def test_a_damaged_database_is_moved_aside_and_a_new_one_started(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "history.db"
    garbage = b"this is not a database " * 100
    db.write_bytes(garbage)
    store = History(db, clock=lambda: START)
    with caplog.at_level(logging.WARNING, logger=history.__name__):
        added = store.add(entry())
    # The backup name has the time in local time.
    backup = db.with_name(f"history.bad-{START.astimezone():%Y%m%d-%H%M%S}.db")
    assert backup.read_bytes() == garbage
    assert backup.name in caplog.text
    assert store.recent() == [added]


class OverlapProbe:
    """Holds threads inside the damaged file while another one renames it, and counts them.

    The first thread to open the damaged file goes ahead. Any other thread's connection to it
    stays open until the rename has been tried, and the rename waits briefly for one to arrive.
    Code that lets threads open the file during a repair is therefore caught with the file open
    on every OS, not only when Windows happens to refuse the rename.
    """

    def __init__(self, damaged: Path) -> None:
        self.damaged = damaged
        self.open_at_rename: list[int] = []
        self._open = 0
        self._first_thread: int | None = None
        self._lock = threading.Lock()
        self._renamed = False
        self._other_arrived = threading.Event()
        self._rename_tried = threading.Event()
        self._real_connect = sqlite3.connect
        self._real_replace = os.replace

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(history.sqlite3, "connect", self.connect)
        monkeypatch.setattr(history.os, "replace", self.replace)

    def connect(self, database: Path, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        db = self._real_connect(database, *args, factory=TrackedConnection, **kwargs)
        with self._lock:
            if self._renamed or Path(database) != self.damaged:
                return db
            db.on_close = self._closed
            self._open += 1
            if self._first_thread is None:
                self._first_thread = threading.get_ident()
            is_first_thread = self._first_thread == threading.get_ident()
        if not is_first_thread:
            self._other_arrived.set()
            self._rename_tried.wait(timeout=5)
        return db

    def replace(self, src: str | Path, dst: str | Path) -> None:
        if Path(src) == self.damaged:
            # Give a thread outside the repair the chance to open the damaged file first.
            self._other_arrived.wait(timeout=0.5)
            with self._lock:
                self.open_at_rename.append(self._open)
                self._renamed = True
        try:
            self._real_replace(src, dst)
        finally:
            self._rename_tried.set()

    def _closed(self) -> None:
        with self._lock:
            self._open -= 1


class TrackedConnection(sqlite3.Connection):
    """A connection that reports its first close, so a test can count open ones."""

    on_close: Callable[[], None] | None = None

    def close(self) -> None:
        callback, self.on_close = self.on_close, None
        super().close()
        if callback:
            callback()


def test_no_thread_has_the_damaged_file_open_while_it_is_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "history.db"
    db.write_bytes(b"this is not a database " * 100)
    probe = OverlapProbe(db)
    probe.install(monkeypatch)
    store = History(db, clock=StepClock())
    start = threading.Barrier(4)

    def add_after_start(n: int) -> HistoryEntry:
        start.wait(timeout=5)
        return store.add(entry(title=f"Track {n}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add_after_start, range(4)))
    assert probe.open_at_rename == [0]
    assert len(store.recent(limit=100)) == 4
    assert len(list(tmp_path.glob("history.bad-*.db"))) == 1


def lock_error(winerror: int) -> OSError:
    """What Windows raises while another program has the file open for a moment."""
    error = PermissionError(13, "The process cannot access the file")
    error.winerror = winerror
    return error


class RecordingSleep:
    """A stand-in for time.sleep that only notes how long it was asked to wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def damaged_store(tmp_path: Path, sleep: RecordingSleep) -> History:
    db = tmp_path / "history.db"
    db.write_bytes(b"this is not a database " * 100)
    return History(db, clock=lambda: START, sleep=sleep)


@pytest.mark.parametrize("winerror", [5, 32])
def test_a_briefly_locked_damaged_file_is_renamed_on_a_later_try(
    winerror: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep = RecordingSleep()
    store = damaged_store(tmp_path, sleep)
    real_replace = os.replace
    failures = [lock_error(winerror), lock_error(winerror)]

    def locked_twice(src: str | Path, dst: str | Path) -> None:
        if failures:
            raise failures.pop()
        real_replace(src, dst)

    monkeypatch.setattr(history.os, "replace", locked_twice)
    store.add(entry())
    assert sleep.waits == [0.1, 0.2]
    assert len(list(tmp_path.glob("history.bad-*.db"))) == 1


def test_a_damaged_file_that_stays_locked_is_a_disk_error_after_about_two_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep = RecordingSleep()
    store = damaged_store(tmp_path, sleep)

    def always_locked(src: str | Path, dst: str | Path) -> None:
        raise lock_error(32)

    monkeypatch.setattr(history.os, "replace", always_locked)
    with pytest.raises(ScmpError) as error_info:
        store.add(entry())
    assert error_info.value.kind is ErrorKind.disk
    assert len(sleep.waits) == 5
    assert sum(sleep.waits) == pytest.approx(2.0)


def test_a_rename_error_that_isnt_a_lock_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep = RecordingSleep()
    store = damaged_store(tmp_path, sleep)

    def denied(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(history.os, "replace", denied)
    with pytest.raises(ScmpError) as error_info:
        store.add(entry())
    assert error_info.value.kind is ErrorKind.disk
    assert sleep.waits == []


def test_a_rename_error_without_an_os_message_still_explains_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = damaged_store(tmp_path, RecordingSleep())

    def odd_failure(src: str | Path, dst: str | Path) -> None:
        raise OSError("the backup name is taken")

    monkeypatch.setattr(history.os, "replace", odd_failure)
    with pytest.raises(ScmpError) as error_info:
        store.add(entry())
    assert error_info.value.detail == (
        "history.db is damaged and couldn't be moved aside: the backup name is taken"
    )


def test_a_folder_error_without_an_os_message_still_explains_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def odd_failure(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("the drive went away")

    monkeypatch.setattr(Path, "mkdir", odd_failure)
    with pytest.raises(ScmpError) as error_info:
        History(tmp_path / "new" / "history.db").recent()
    assert error_info.value.detail == "can't create the data folder: the drive went away"


class SchemaCounter:
    """Counts how many connections run the schema script."""

    def __init__(self) -> None:
        self.runs = 0
        self._real_connect = sqlite3.connect

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(history.sqlite3, "connect", self.connect)

    def connect(self, database: Path, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        db = self._real_connect(database, *args, factory=CountingConnection, **kwargs)
        db.counter = self
        return db


class CountingConnection(sqlite3.Connection):
    counter: SchemaCounter | None = None

    def executescript(self, script: str) -> sqlite3.Cursor:
        if self.counter is not None and script == history.SCHEMA:
            self.counter.runs += 1
        return super().executescript(script)


def test_the_schema_runs_only_on_the_first_connection(
    store: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = SchemaCounter()
    counter.install(monkeypatch)
    added = store.add(entry())
    assert store.search("drive") == [added]
    assert store.recent() == [added]
    assert store.find_by_url(added.url_norm or "") == added
    assert counter.runs == 1


def test_a_history_file_deleted_meanwhile_is_started_again(store: History) -> None:
    store.add(entry())
    store.path.unlink()
    assert store.recent() == []
    added = store.add(entry(title="Night Bus"))
    assert store.recent() == [added]


def test_a_file_damaged_after_it_was_opened_is_replaced_by_the_next_call(
    tmp_path: Path,
) -> None:
    store = History(tmp_path / "history.db", clock=lambda: START)
    store.add(entry())
    store.path.write_bytes(b"this is not a database " * 100)
    # The call that finds the damage fails, and the next one starts a new file.
    with pytest.raises(ScmpError) as error_info:
        store.recent()
    assert error_info.value.kind is ErrorKind.disk
    assert store.recent() == []
    assert len(list(tmp_path.glob("history.bad-*.db"))) == 1


def test_a_re_tag_gives_every_save_of_the_file_its_new_tags(store: History) -> None:
    first = store.add(entry())
    store.add(entry())
    other = store.add(entry(title="Night Bus"))
    changed = store.update_tags(
        first.saved_path, "Midnight Drive (VIP)", "Kaia, Lou V", "Night Shift"
    )
    assert changed == 2
    titles = sorted((found.title, found.artist, found.album) for found in store.recent())
    assert titles == [
        ("Midnight Drive (VIP)", "Kaia, Lou V", "Night Shift"),
        ("Midnight Drive (VIP)", "Kaia, Lou V", "Night Shift"),
        ("Night Bus", "Kaia", "Midnight Drive"),
    ]
    assert store.find_by_source_id("soundcloud:tracks:1") is not None
    assert other.saved_path != first.saved_path


def test_a_re_tag_of_a_file_sc_mp3_never_saved_changes_nothing(store: History) -> None:
    store.add(entry())
    assert store.update_tags(Path("elsewhere") / "song.mp3", "Song", "Kaia", "") == 0
    assert [found.title for found in store.recent()] == ["Midnight Drive"]
