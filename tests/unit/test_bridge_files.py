"""Tests for the file bridge calls: the pickers, showing a file, and opening Spotify."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scmp3.app import messages
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.desktop import Desktop
from scmp3.app.events import EventBus
from scmp3.core import paths
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore


class FakeWindow:
    def __init__(self, answer: Sequence[str] | None) -> None:
        self.answer = answer
        self.asked: list[dict[str, Any]] = []

    def create_file_dialog(self, dialog_type: int = 10, **options: Any) -> Sequence[str] | None:
        self.asked.append({"dialog_type": dialog_type, **options})
        return self.answer


class FakeOS:
    def __init__(self) -> None:
        self.spawned: list[list[str]] = []
        self.links: list[str] = []
        self.spotify_installed = True

    def start_file(self, link: str) -> None:
        if not self.spotify_installed:
            raise OSError("no handler")
        self.links.append(link)


@pytest.fixture
def os_fake() -> FakeOS:
    return FakeOS()


@pytest.fixture
def desktop(os_fake: FakeOS) -> Desktop:
    return Desktop(
        platform="win32",
        spawn=os_fake.spawned.append,
        start_file=os_fake.start_file,
        dialog_type={"open": 10, "folder": 20}.__getitem__,
    )


@pytest.fixture
def api(desktop: Desktop, tmp_path: Path) -> Api:
    store = SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(tmp_path / "library")})
    context = AppContext(
        settings=store,
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        desktop=desktop,
    )
    return Api(context)


def test_the_folder_picker_starts_in_the_library_folder(
    api: Api, desktop: Desktop, tmp_path: Path
) -> None:
    window = FakeWindow(("D:\\Music",))
    desktop.attach(window)
    assert api.pick_folder() == {"path": "D:\\Music"}
    assert window.asked[0]["dialog_type"] == 20
    assert window.asked[0]["directory"] == str(tmp_path / "library")


def test_a_cancelled_folder_picker_gives_no_path(api: Api, desktop: Desktop) -> None:
    desktop.attach(FakeWindow(None))
    assert api.pick_folder() == {"path": None}


def test_the_audio_picker_takes_several_mp3_and_m4a_files(api: Api, desktop: Desktop) -> None:
    window = FakeWindow(("C:\\a.mp3", "C:\\b.m4a"))
    desktop.attach(window)
    assert api.pick_audio_files() == {"paths": ["C:\\a.mp3", "C:\\b.m4a"]}
    assert window.asked[0]["allow_multiple"] is True
    assert window.asked[0]["file_types"] == (messages.AUDIO_FILES,)


def test_a_cancelled_audio_picker_gives_no_paths(api: Api, desktop: Desktop) -> None:
    desktop.attach(FakeWindow(None))
    assert api.pick_audio_files() == {"paths": []}


def test_the_image_picker_takes_one_image(api: Api, desktop: Desktop) -> None:
    window = FakeWindow(("C:\\cover.png",))
    desktop.attach(window)
    assert api.pick_image() == {"path": "C:\\cover.png"}
    assert window.asked[0]["allow_multiple"] is False
    assert window.asked[0]["file_types"] == (messages.IMAGE_FILES,)


def test_the_filters_are_ones_pywebview_accepts() -> None:
    from webview.util import parse_file_type

    for filter_text in (messages.AUDIO_FILES, messages.IMAGE_FILES):
        parse_file_type(filter_text)


@pytest.mark.parametrize("call", ["pick_folder", "pick_audio_files", "pick_image"])
def test_a_picker_before_the_window_opens_answers_internal(api: Api, call: str) -> None:
    assert getattr(api, call)() == {"error": "internal"}


def test_a_saved_file_is_shown_in_explorer(api: Api, os_fake: FakeOS, tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio")
    assert api.reveal(str(song)) == {}
    assert os_fake.spawned == [["explorer", "/select,", str(song)]]


def test_showing_a_file_that_isnt_there_says_so(api: Api, tmp_path: Path) -> None:
    assert api.reveal(str(tmp_path / "gone" / "song.mp3")) == {"error": "file_missing"}


@pytest.mark.parametrize("path", [None, "song.mp3", "x" * 8_001])
def test_showing_a_file_needs_its_full_path(api: Api, path: Any, os_fake: FakeOS) -> None:
    assert api.reveal(path) == {"error": "internal"}
    assert os_fake.spawned == []


def test_spotify_opens_through_its_link(api: Api, os_fake: FakeOS) -> None:
    assert api.open_spotify() == {}
    assert os_fake.links == ["spotify:"]


def test_spotify_that_wont_open_says_so(api: Api, os_fake: FakeOS) -> None:
    os_fake.spotify_installed = False
    assert api.open_spotify() == {"error": "spotify_missing"}
