"""Tests for the exclusive file lock, held by a second handle or by a second process."""

import errno
import subprocess
import sys
from pathlib import Path

import pytest

from scmp3.core import filelock
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.filelock import RETRY_EVERY, FileLock, LockTimeout, LockUnavailable


class FakeTime:
    """A clock that only moves when sleep is called, and a record of the sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "locks" / "test.lock"


def test_the_lock_is_taken_and_given_back(lock_path: Path) -> None:
    first = FileLock(lock_path, 0)
    first.acquire()
    first.release()
    with FileLock(lock_path, 0):
        pass


def test_the_file_and_its_folder_are_made_and_left_in_place(lock_path: Path) -> None:
    with FileLock(lock_path, 0):
        assert lock_path.is_file()
    assert lock_path.is_file()


def test_a_second_handle_cant_take_a_held_lock(lock_path: Path) -> None:
    with FileLock(lock_path, 0), pytest.raises(LockTimeout):
        FileLock(lock_path, 0).acquire()


def test_a_timeout_of_0_tries_once_without_sleeping(lock_path: Path) -> None:
    time = FakeTime()
    with FileLock(lock_path, 0), pytest.raises(LockTimeout):
        FileLock(lock_path, 0, clock=time.clock, sleep=time.sleep).acquire()
    assert time.sleeps == []


def test_a_held_lock_is_tried_again_until_the_timeout(lock_path: Path) -> None:
    time = FakeTime()
    # Just under ten waits, so float rounding in the sum can't add an eleventh.
    timeout = 10 * RETRY_EVERY - RETRY_EVERY / 2
    with FileLock(lock_path, 0), pytest.raises(LockTimeout):
        FileLock(lock_path, timeout, clock=time.clock, sleep=time.sleep).acquire()
    assert time.sleeps == [RETRY_EVERY] * 10


def test_the_lock_is_taken_once_the_holder_lets_go(lock_path: Path) -> None:
    holder = FileLock(lock_path, 0)
    holder.acquire()
    time = FakeTime()

    def sleep(seconds: float) -> None:
        time.sleep(seconds)
        # The other holder finishes during the third wait.
        if len(time.sleeps) == 3:
            holder.release()

    with FileLock(lock_path, 5.0, clock=time.clock, sleep=sleep):
        assert len(time.sleeps) == 3


def test_releasing_twice_or_without_the_lock_does_nothing(lock_path: Path) -> None:
    lock = FileLock(lock_path, 0)
    lock.release()
    lock.acquire()
    lock.release()
    lock.release()


def test_a_folder_that_cant_be_made_is_a_disk_error(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    with pytest.raises(LockUnavailable) as error_info:
        FileLock(blocker / "test.lock", 0).acquire()
    assert error_info.value.kind is ErrorKind.disk


def test_a_lock_the_system_refuses_is_unavailable_and_closes_its_file(
    lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(handle: object) -> bool:
        # What flock gives on an NFS home folder with no lock service.
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(filelock, "_try_lock", refuse)
    with pytest.raises(LockUnavailable) as error_info:
        FileLock(lock_path, 5).acquire()
    assert isinstance(error_info.value, ScmpError)
    assert error_info.value.kind is ErrorKind.disk
    assert str(lock_path.parent) not in str(error_info.value)
    # Windows can't delete a file that's still open, so this also shows the handle was closed.
    lock_path.unlink()


# A second process that takes the lock, says so, and holds it until it reads a line.
HOLDER = """
import sys
from pathlib import Path
from scmp3.core.filelock import FileLock

with FileLock(Path(sys.argv[1]), 10):
    print("locked", flush=True)
    sys.stdin.readline()
"""


def test_a_lock_held_by_another_process_is_refused_until_it_ends(lock_path: Path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(LockTimeout):
            FileLock(lock_path, 0).acquire()
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=20) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
    with FileLock(lock_path, 0):
        pass
