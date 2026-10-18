"""Tests for the saved-files history in sqlite."""

import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

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


def test_threads_that_find_a_damaged_database_replace_it_once(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    db.write_bytes(b"this is not a database " * 100)
    store = History(db, clock=StepClock())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.add(entry(title=f"Track {n}")), range(8)))
    assert len(store.recent(limit=100)) == 8
    assert len(list(tmp_path.glob("history.bad-*.db"))) == 1
