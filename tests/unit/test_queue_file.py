"""Tests for queue.json on disk, and for the thread that writes it after each change."""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from scmp3.app import queue_file
from scmp3.app.queue_file import QueueFile
from scmp3.app.queue_writer import QueueWriter
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError

WHEN = datetime(2026, 10, 21, 18, 30, 0)
WAIT = 5.0


def entry(job_id: str) -> str:
    return json.dumps({"job": {"id": job_id}})


@pytest.fixture
def file(tmp_path: Path) -> QueueFile:
    return QueueFile(tmp_path / "queue.json", clock=lambda: WHEN)


def backups(file: QueueFile) -> list[Path]:
    return sorted(file.path.parent.glob("queue.bad-*.json"))


# The file


def test_queue_json_lives_in_the_data_folder(scmp3_home: Path) -> None:
    assert queue_file.default_path() == paths.data_dir() / "queue.json"


def test_no_file_means_an_empty_queue(file: QueueFile) -> None:
    assert file.load() == []


def test_saved_entries_load_back_in_order(file: QueueFile) -> None:
    file.save([entry("a"), entry("b")])
    assert file.load() == [{"job": {"id": "a"}}, {"job": {"id": "b"}}]
    assert json.loads(file.path.read_text(encoding="utf-8"))["version"] == 1


def test_an_empty_queue_is_saved_too(file: QueueFile) -> None:
    file.save([entry("a")])
    file.save([])
    assert file.load() == []


def test_a_byte_order_mark_is_fine(file: QueueFile) -> None:
    file.path.write_bytes(b"\xef\xbb\xbf" + b'{"version": 1, "jobs": [{"job": {}}]}')
    assert file.load() == [{"job": {}}]


@pytest.mark.parametrize(
    "content",
    [
        b"{ not json",
        b"\xff\xfe not text",
        b"[]",
        b'{"version": 2, "jobs": []}',
        b'{"version": 1, "jobs": {}}',
        b'{"version": 1, "jobs": [3]}',
    ],
    ids=[
        "not json",
        "not utf-8",
        "a list",
        "a newer version",
        "jobs not a list",
        "a job not an object",
    ],
)
def test_a_file_that_cant_be_used_is_moved_aside(
    file: QueueFile, content: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    file.path.write_bytes(content)
    with caplog.at_level(logging.WARNING, logger=queue_file.__name__):
        assert file.load() == []
    (backup,) = backups(file)
    assert backup.name == "queue.bad-20261021-183000.json"
    assert backup.read_bytes() == content
    assert backup.name in caplog.text
    # The next save starts a new file.
    file.save([entry("a")])
    assert file.load() == [{"job": {"id": "a"}}]


def test_a_file_that_cant_be_opened_is_never_written_over(file: QueueFile) -> None:
    # A folder in its place can't be read, like a file another program has locked.
    file.path.mkdir()
    assert file.load() == []
    with pytest.raises(ScmpError) as error_info:
        file.save([entry("a")])
    assert error_info.value.kind is ErrorKind.disk
    assert file.path.is_dir()


def test_moving_aside_a_file_that_has_gone_does_nothing(file: QueueFile) -> None:
    file.move_aside("a test")
    assert backups(file) == []
    file.save([entry("a")])
    assert file.load() == [{"job": {"id": "a"}}]


# The writer


class Entries:
    """Stands in for the job manager's entries, counting how often they're asked for."""

    def __init__(self) -> None:
        self.current: list[str] = []
        self.calls = 0

    def __call__(self) -> list[str]:
        self.calls += 1
        return list(self.current)


def wait_for(done: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT
    while not done():
        assert time.monotonic() < deadline, "the write never came"
        time.sleep(0.01)


def test_a_change_is_written_after_the_delay(file: QueueFile) -> None:
    entries = Entries()
    writer = QueueWriter(file, entries, delay=0.05)
    writer.start()
    entries.current = [entry("a")]
    writer.changed()
    wait_for(lambda: file.path.exists())
    assert file.load() == [{"job": {"id": "a"}}]
    writer.close()


def test_changes_close_together_go_into_one_write(file: QueueFile) -> None:
    entries = Entries()
    writer = QueueWriter(file, entries, delay=0.3)
    writer.start()
    for job_id in "abc":
        entries.current.append(entry(job_id))
        writer.changed()
    wait_for(lambda: entries.calls == 1)
    time.sleep(0.4)
    assert entries.calls == 1
    assert len(file.load()) == 3
    writer.close()


def test_nothing_is_written_without_a_change(file: QueueFile) -> None:
    entries = Entries()
    writer = QueueWriter(file, entries, delay=0.01)
    writer.start()
    time.sleep(0.1)
    assert entries.calls == 0
    writer.close()


def test_closing_writes_a_last_time_and_stops_the_thread(file: QueueFile) -> None:
    before = set(threading.enumerate())
    entries = Entries()
    writer = QueueWriter(file, entries, delay=60.0)
    writer.start()
    started = set(threading.enumerate()) - before
    entries.current = [entry("a")]
    writer.changed()
    assert writer.close() is True
    assert file.load() == [{"job": {"id": "a"}}]
    assert [thread.name for thread in started] == ["scmp3-queue"]
    assert not any(thread.is_alive() for thread in started)
    # A change after closing is ignored.
    writer.changed()
    assert entries.calls == 1


def test_a_write_that_fails_is_logged_and_says_so(
    file: QueueFile, caplog: pytest.LogCaptureFixture
) -> None:
    file.path.mkdir()
    file.load()
    writer = QueueWriter(file, Entries())
    with caplog.at_level(logging.WARNING, logger="scmp3.app.queue_writer"):
        assert writer.write_now() is False
    assert "The queue couldn't be kept in queue.json" in caplog.text


def test_writing_now_works_without_the_thread(file: QueueFile) -> None:
    entries = Entries()
    entries.current = [entry("a")]
    assert QueueWriter(file, entries).write_now() is True
    assert file.load() == [{"job": {"id": "a"}}]


def test_a_bug_while_building_the_entries_is_logged_and_the_writer_goes_on(
    file: QueueFile, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def entries() -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TypeError("a bug in an entry")
        return [entry("a")]

    writer = QueueWriter(file, entries, delay=0.01)
    writer.start()
    with caplog.at_level(logging.ERROR, logger="scmp3.app.queue_writer"):
        writer.changed()
        wait_for(lambda: calls == 1)
    writer.changed()
    wait_for(lambda: file.path.exists())
    assert writer.close() is True
    assert "Keeping the queue in queue.json failed unexpectedly" in caplog.text


class CountingFile(QueueFile):
    """queue.json, counting the writes that reach the disk."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.saves = 0

    def save(self, entries: list[str]) -> None:
        self.saves += 1
        super().save(entries)


def test_a_queue_that_hasnt_changed_isnt_written_again(tmp_path: Path) -> None:
    file = CountingFile(tmp_path / "queue.json")
    entries = Entries()
    entries.current = [entry("a")]
    writer = QueueWriter(file, entries)
    assert writer.write_now() is True
    assert writer.write_now() is True
    assert file.saves == 1
    entries.current.append(entry("b"))
    assert writer.write_now() is True
    assert file.saves == 2


def test_writes_come_at_most_once_an_interval_while_changes_keep_coming(tmp_path: Path) -> None:
    file = CountingFile(tmp_path / "queue.json")
    entries = Entries()
    writer = QueueWriter(file, entries, delay=0.01, interval=0.4)
    writer.start()
    entries.current = [entry("a")]
    writer.changed()
    wait_for(lambda: file.saves == 1)
    first = time.monotonic()
    entries.current = [entry("a"), entry("b")]
    writer.changed()
    wait_for(lambda: file.saves == 2)
    assert time.monotonic() - first >= 0.3
    writer.close()
