"""Tests for file dialogs, the file manager and opening Spotify, with the OS faked."""

import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scmp3.app.desktop import Desktop
from scmp3.core.errors import ErrorKind, ScmpError

DIALOG_TYPES = {"open": 10, "folder": 20}


class FakeWindow:
    """A window whose dialogs answer with what the test set."""

    def __init__(self, answer: Sequence[str] | None = None) -> None:
        self.answer = answer
        self.asked: list[dict[str, Any]] = []
        self.while_open: Any = None

    def create_file_dialog(self, dialog_type: int = 10, **options: Any) -> Sequence[str] | None:
        self.asked.append({"dialog_type": dialog_type, **options})
        if self.while_open is not None:
            self.while_open()
        return self.answer


class Recorder:
    """Records the programs and links the desktop would start."""

    def __init__(self, opener_works: bool = True, start_fails: bool = False) -> None:
        self.spawned: list[list[str]] = []
        self.opened: list[list[str]] = []
        self.started: list[str] = []
        self.opener_works = opener_works
        self.start_fails = start_fails

    def spawn(self, command: list[str]) -> None:
        self.spawned.append(command)

    def run_opener(self, command: list[str]) -> bool:
        self.opened.append(command)
        return self.opener_works

    def start_file(self, link: str) -> None:
        self.started.append(link)
        if self.start_fails:
            raise OSError(1155, "No application is associated with the specified file")


def desktop(platform: str = "win32", recorder: Recorder | None = None) -> tuple[Desktop, Recorder]:
    recorder = recorder or Recorder()
    made = Desktop(
        platform=platform,
        spawn=recorder.spawn,
        run_opener=recorder.run_opener,
        start_file=recorder.start_file,
        dialog_type=DIALOG_TYPES.__getitem__,
    )
    return made, recorder


# Dialogs


def test_a_dialog_before_the_window_opens_is_a_bug() -> None:
    made, _ = desktop()
    with pytest.raises(ScmpError) as raised:
        made.choose("folder")
    assert raised.value.kind == ErrorKind.internal


def test_a_dialog_gets_its_type_folder_and_filters(tmp_path: Path) -> None:
    made, _ = desktop()
    window = FakeWindow(answer=("C:\\Music\\a.mp3", "C:\\Music\\b.m4a"))
    made.attach(window)
    chosen = made.choose(
        "open", directory=tmp_path, multiple=True, file_types=("MP3 files (*.mp3)",)
    )
    assert chosen == ["C:\\Music\\a.mp3", "C:\\Music\\b.m4a"]
    assert window.asked == [
        {
            "dialog_type": 10,
            "directory": str(tmp_path),
            "allow_multiple": True,
            "file_types": ("MP3 files (*.mp3)",),
        }
    ]


def test_a_cancelled_dialog_chooses_nothing() -> None:
    made, _ = desktop()
    made.attach(FakeWindow(answer=None))
    assert made.choose("folder") == []


def test_only_one_dialog_opens_at_a_time() -> None:
    made, _ = desktop()
    window = FakeWindow(answer=("C:\\Music",))
    second: list[list[str]] = []
    window.while_open = lambda: second.append(made.choose("folder"))
    made.attach(window)
    assert made.choose("folder") == ["C:\\Music"]
    assert second == [[]]
    assert len(window.asked) == 1


def test_a_dialog_can_open_again_after_one_fails() -> None:
    made, _ = desktop()

    class Failing(FakeWindow):
        def create_file_dialog(self, dialog_type: int = 10, **options: Any) -> Any:
            raise RuntimeError("the dialog failed")

    made.attach(Failing())
    with pytest.raises(RuntimeError):
        made.choose("folder")
    made.attach(FakeWindow(answer=("C:\\Music",)))
    assert made.choose("folder") == ["C:\\Music"]


def test_dialogs_from_two_threads_never_overlap() -> None:
    made, _ = desktop()
    window = FakeWindow(answer=("C:\\Music",))
    open_now = threading.Event()
    release = threading.Event()

    def slow() -> None:
        open_now.set()
        release.wait(5)

    window.while_open = slow
    made.attach(window)
    results: list[list[str]] = []
    first = threading.Thread(target=lambda: results.append(made.choose("folder")))
    first.start()
    assert open_now.wait(5)
    assert made.choose("folder") == []
    release.set()
    first.join(5)
    assert results == [["C:\\Music"]]


# The file manager


@pytest.mark.parametrize(
    ("platform", "command"),
    [
        ("win32", lambda path: ["explorer", "/select,", str(path)]),
        ("darwin", lambda path: ["open", "-R", str(path)]),
        ("linux", lambda path: ["xdg-open", str(path.parent)]),
    ],
)
def test_a_saved_file_is_shown_selected_in_its_folder(
    tmp_path: Path, platform: str, command: Any
) -> None:
    song = tmp_path / "Kaia - Midnight Drive.mp3"
    song.write_bytes(b"audio")
    made, recorder = desktop(platform)
    assert made.reveal(song) is True
    assert recorder.spawned == [command(song)]


@pytest.mark.parametrize(
    ("platform", "program"), [("win32", "explorer"), ("darwin", "open"), ("linux", "xdg-open")]
)
def test_a_file_that_has_gone_shows_its_folder(tmp_path: Path, platform: str, program: str) -> None:
    made, recorder = desktop(platform)
    assert made.reveal(tmp_path / "moved.mp3") is True
    assert recorder.spawned == [[program, str(tmp_path)]]


def test_explorer_gets_the_switch_and_a_path_with_spaces_apart(tmp_path: Path) -> None:
    # Joined, subprocess quotes the pair for the space, and Explorer opens Documents instead.
    folder = tmp_path / "reveal test"
    folder.mkdir()
    song = folder / "Kaia, Lou V - Midnight Drive.mp3"
    song.write_bytes(b"audio")
    made, recorder = desktop("win32")
    made.reveal(song)
    assert recorder.spawned == [["explorer", "/select,", str(song)]]
    assert subprocess.list2cmdline(recorder.spawned[0]).startswith('explorer /select, "')


def test_a_file_whose_folder_has_gone_cant_be_shown(tmp_path: Path) -> None:
    made, recorder = desktop()
    assert made.reveal(tmp_path / "gone" / "song.mp3") is False
    assert recorder.spawned == []


# Opening Spotify


def test_windows_opens_spotify_links_through_its_handler() -> None:
    made, recorder = desktop("win32")
    assert made.open_link("spotify:") is True
    assert recorder.started == ["spotify:"]


def test_windows_without_spotify_says_it_didnt_open() -> None:
    made, _ = desktop("win32", Recorder(start_fails=True))
    assert made.open_link("spotify:") is False


@pytest.mark.parametrize(("platform", "opener"), [("darwin", "open"), ("linux", "xdg-open")])
def test_macos_and_linux_open_spotify_links_with_their_opener(platform: str, opener: str) -> None:
    made, recorder = desktop(platform)
    assert made.open_link("spotify:") is True
    assert recorder.opened == [[opener, "spotify:"]]


def test_an_opener_that_fails_says_the_link_didnt_open() -> None:
    made, _ = desktop("linux", Recorder(opener_works=False))
    assert made.open_link("spotify:") is False


def test_an_opener_that_hangs_or_is_missing_says_the_link_didnt_open() -> None:
    for problem in (subprocess.TimeoutExpired("xdg-open", 10), FileNotFoundError("xdg-open")):

        def run_opener(command: list[str], problem: Exception = problem) -> bool:
            raise problem

        made = Desktop(platform="linux", run_opener=run_opener)
        assert made.open_link("spotify:") is False
