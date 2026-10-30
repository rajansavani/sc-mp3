"""Tests for reading the files of a drop on the window from GTK, as WebKitGTK needs."""

import os
import socket
import sys
from typing import Any

import pytest

from scmp3.app.drag_paths import (
    DragPaths,
    DragRecorder,
    drag_uris,
    find_widget,
    local_path,
    local_paths,
)

SONG_URI = "file:///home/sam/Music/my%20song.mp3"
SONG = "/home/sam/Music/my song.mp3"
COVER_URI = "file:///home/sam/Pictures/cover.jpg"
COVER = "/home/sam/Pictures/cover.jpg"
OWN_NAME = "sams-laptop"


class FakeWatch:
    """Stands in for GTK's drag signals, and keeps what it was asked to report to."""

    def __init__(self) -> None:
        self.watched: list[Any] = []
        self.recorder: DragRecorder | None = None

    def __call__(self, native: Any, recorder: DragRecorder) -> None:
        self.watched.append(native)
        self.recorder = recorder


def gtk_paths(watch: FakeWatch | None = None) -> DragPaths:
    return DragPaths(renderer=lambda: "gtkwebkit2", watch_drags=watch or FakeWatch())


class Drag:
    """Stands in for GTK's drag context, which is the same object for a drag's data and drop."""


class Window:
    native = "the GTK window"


def test_a_file_uri_becomes_its_path_with_spaces_decoded() -> None:
    assert local_path(SONG_URI) == SONG


@pytest.mark.parametrize(
    ("uri", "path"),
    [
        ("file:///tmp/song #1.mp3", "/tmp/song #1.mp3"),
        ("file:///tmp/what?.mp3", "/tmp/what?.mp3"),
        ("file:///tmp/what?", "/tmp/what?"),
        ("file:///tmp/a%23b%3F.mp3", "/tmp/a#b?.mp3"),
        ("file:/tmp/song.mp3", "/tmp/song.mp3"),
    ],
)
def test_a_raw_hash_or_question_mark_stays_in_the_path(uri: str, path: str) -> None:
    # Some sources write paths into their uri-list without encoding them.
    assert local_path(uri, OWN_NAME) == path


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", OWN_NAME, OWN_NAME.upper()])
def test_a_file_uri_may_name_this_computer(host: str) -> None:
    assert local_path(f"file://{host}/tmp/song.mp3", OWN_NAME) == "/tmp/song.mp3"


def test_this_computers_name_comes_from_the_system() -> None:
    assert local_path(f"file://{socket.gethostname()}/tmp/song.mp3") == "/tmp/song.mp3"


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/cover.jpg",
        "file://nas/share/song.mp3",
        "file://nas",
        "file:song.mp3",
        "song.mp3",
        "",
    ],
)
def test_links_and_files_on_other_computers_have_no_local_path(uri: str) -> None:
    assert local_path(uri, OWN_NAME) is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows names files in UTF-16, not bytes")
def test_a_name_that_isnt_utf8_keeps_its_bytes() -> None:
    path = local_path("file:///tmp/caf%E9.mp3")
    assert path is not None
    assert os.fsencode(path) == b"/tmp/caf\xe9.mp3"


def test_a_uri_list_gives_each_local_file_once_in_order() -> None:
    uris = [SONG_URI, "https://example.com/page", COVER_URI, SONG_URI]
    assert local_paths(uris) == [SONG, COVER]


def test_a_drop_takes_its_drags_files_once() -> None:
    paths = gtk_paths()
    drag = Drag()
    paths.record(drag, [SONG_URI, COVER_URI])
    paths.drop(drag)
    assert paths.take() == [SONG, COVER]
    assert paths.take() == []


def test_a_drag_that_wasnt_dropped_gives_nothing() -> None:
    # Let go on the bare page, which refuses it, or outside the window: GTK reports no drop.
    paths = gtk_paths()
    paths.record(Drag(), [COVER_URI])
    assert paths.take() == []


def test_a_later_drop_without_files_doesnt_get_an_earlier_drags() -> None:
    # A cover let go elsewhere, then plain text dropped on the Replace dialog (review F1).
    paths = gtk_paths()
    paths.record(Drag(), [COVER_URI])
    paths.drop(Drag())
    assert paths.take() == []


def test_a_drop_nobody_took_is_gone_at_the_next_drop() -> None:
    paths = gtk_paths()
    drag = Drag()
    paths.record(drag, [COVER_URI])
    paths.drop(drag)
    paths.drop(Drag())
    assert paths.take() == []


def test_the_uri_list_asked_for_twice_in_one_drag_gives_each_file_once() -> None:
    # WebKitGTK asks again when the drag leaves the window and comes back.
    paths = gtk_paths()
    drag = Drag()
    paths.record(drag, [SONG_URI])
    paths.record(drag, [SONG_URI])
    paths.drop(drag)
    assert paths.take() == [SONG]


def test_a_new_drag_replaces_one_that_was_never_dropped() -> None:
    paths = gtk_paths()
    paths.record(Drag(), [COVER_URI])
    drag = Drag()
    paths.record(drag, [SONG_URI])
    paths.drop(drag)
    assert paths.take() == [SONG]


def test_a_drag_of_links_gives_no_files() -> None:
    paths = gtk_paths()
    paths.record(Drag(), [SONG_URI])
    drag = Drag()
    paths.record(drag, ["https://soundcloud.com/artist/track"])
    paths.drop(drag)
    assert paths.take() == []


def test_watching_starts_once_with_the_native_window_on_gtk() -> None:
    watch = FakeWatch()
    paths = gtk_paths(watch)
    paths.watch(Window())
    paths.watch(Window())
    assert watch.watched == ["the GTK window"]
    assert watch.recorder is paths


@pytest.mark.parametrize("renderer", ["qtwebengine", "edgechromium", "wkwebview", None])
def test_other_webviews_arent_watched(renderer: str | None) -> None:
    watch = FakeWatch()
    DragPaths(renderer=lambda: renderer, watch_drags=watch).watch(Window())
    assert watch.watched == []


class Target:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class SelectionData:
    """Stands in for GTK's selection data, which holds one target's data."""

    def __init__(self, target: str, uris: list[str] | None) -> None:
        self._target = Target(target)
        self._uris = uris

    def get_target(self) -> Target:
        return self._target

    def get_uris(self) -> list[str] | None:
        return self._uris


def test_a_uri_list_target_gives_its_uris() -> None:
    assert drag_uris(SelectionData("text/uri-list", [SONG_URI])) == [SONG_URI]
    assert drag_uris(SelectionData("text/uri-list", None)) == []


def test_other_targets_give_no_uris() -> None:
    # WebKitGTK asks for text/plain too, which pywebview reads.
    assert drag_uris(SelectionData("text/plain;charset=utf-8", None)) is None


class Widget:
    def __init__(self, *children: "Widget") -> None:
        self._children = list(children)

    def get_children(self) -> list["Widget"]:
        return self._children


class WebView(Widget):
    pass


def test_the_webkit_view_is_found_inside_the_window() -> None:
    view = WebView()
    window = Widget(Widget(Widget(), view))
    assert find_widget(window, WebView) is view


def test_a_window_without_a_webkit_view_gives_none() -> None:
    assert find_widget(Widget(Widget()), WebView) is None
