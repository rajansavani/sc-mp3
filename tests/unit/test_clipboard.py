"""Tests for the clipboard offer: which link is offered, and reading the clipboard on each OS.

No test reads or writes the real clipboard. The Windows calls are faked with a buffer.
"""

import ctypes
import logging
import subprocess
from collections.abc import Sequence

import pytest

from scmp3.app import clipboard
from scmp3.app.clipboard import offered_link, read_clipboard, read_linux, read_mac, read_windows

TRACK = "https://soundcloud.com/kaia/midnight-drive"
OTHER_TRACK = "https://soundcloud.com/mara-lune/lantern"


def nothing_queued(url: str) -> bool:
    return False


@pytest.mark.parametrize(
    ("text", "offered"),
    [
        (TRACK, TRACK),
        (f"listen to this: {TRACK}?si=abc123 so good", TRACK),
        ("https://www.soundcloud.com/kaia/midnight-drive/", TRACK),
        ("https://on.soundcloud.com/AbCdEfGh", "https://on.soundcloud.com/AbCdEfGh"),
    ],
    ids=["bare", "in chat text", "www and slash", "short link"],
)
def test_a_track_link_is_offered_normalized(text: str, offered: str) -> None:
    assert offered_link(text, nothing_queued) == offered


@pytest.mark.parametrize(
    "text",
    [
        "https://soundcloud.com/kaia",
        "https://soundcloud.com/kaia/sets/night-drives",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://example.com/kaia/midnight-drive",
        "C:/Music/old-favorite.mp3",
        "just some words",
        "",
    ],
    ids=["profile", "set", "youtube", "other site", "file", "words", "empty"],
)
def test_nothing_is_offered_that_adding_would_refuse(text: str) -> None:
    # Sets and YouTube can't be saved in this version, so they aren't offered either.
    assert offered_link(text, nothing_queued) is None


def test_a_queued_link_is_passed_over_for_the_next_one() -> None:
    text = f"{TRACK} and {OTHER_TRACK}"
    assert offered_link(text, lambda url: url == TRACK) == OTHER_TRACK
    assert offered_link(text, lambda url: True) is None


def test_the_queue_is_asked_about_the_normalized_link() -> None:
    asked: list[str] = []
    offered_link(f"{TRACK}?si=abc", lambda url: asked.append(url) or True)
    assert asked == [TRACK]


def test_only_the_start_of_a_huge_clipboard_is_looked_at() -> None:
    text = "x" * clipboard.MAX_CHARACTERS + " " + TRACK
    assert offered_link(text, nothing_queued) is None
    assert offered_link("word " * 5_000 + TRACK, nothing_queued) == TRACK


def test_a_quoted_link_is_offered_as_adding_would_take_it() -> None:
    assert offered_link(f'"{TRACK}"', nothing_queued) == TRACK


class FakeWindowsClipboard:
    """The Win32 clipboard calls, backed by a buffer instead of the real clipboard."""

    def __init__(self, text: str | None, *, busy_for: int = 0, after_end: str = "") -> None:
        # UTF-16 on every OS, as Windows keeps it, then whatever the memory held after the end.
        self.buffer = (
            ctypes.create_string_buffer(f"{text}\0{after_end}".encode("utf-16-le"))
            if text is not None
            else None
        )
        self.busy_for = busy_for
        self.opens = 0
        self.closed = 0
        self.unlocked = 0

    def has_format(self, fmt: int) -> bool:
        return fmt == clipboard.CF_UNICODETEXT and self.buffer is not None

    def open(self, owner: None) -> bool:
        self.opens += 1
        return self.opens > self.busy_for

    def close(self) -> bool:
        self.closed += 1
        return True

    def get_data(self, fmt: int) -> int:
        return 42

    def lock(self, handle: int) -> int:
        assert self.buffer is not None
        return ctypes.addressof(self.buffer)

    def unlock(self, handle: int) -> bool:
        self.unlocked += 1
        return True

    def size(self, handle: int) -> int:
        assert self.buffer is not None
        return ctypes.sizeof(self.buffer)


def no_sleep(seconds: float) -> None:
    pass


def test_windows_text_is_read_up_to_its_end_and_the_clipboard_is_closed() -> None:
    calls = FakeWindowsClipboard(f"{TRACK} ünïcode", after_end="old text")
    assert read_windows(calls, no_sleep) == f"{TRACK} ünïcode"
    assert calls.closed == 1
    assert calls.unlocked == 1


def test_windows_text_outside_the_basic_plane_is_read_whole() -> None:
    # The wave is two UTF-16 units, which make one character.
    calls = FakeWindowsClipboard(f"{TRACK} Low Tide 🌊")
    assert read_windows(calls, no_sleep) == f"{TRACK} Low Tide 🌊"


def test_a_windows_clipboard_without_text_isnt_opened() -> None:
    calls = FakeWindowsClipboard(None)
    assert read_windows(calls, no_sleep) is None
    assert calls.opens == 0


def test_a_windows_clipboard_another_app_holds_is_tried_again_for_a_moment() -> None:
    calls = FakeWindowsClipboard(TRACK, busy_for=2)
    assert read_windows(calls, no_sleep) == TRACK
    held = FakeWindowsClipboard(TRACK, busy_for=clipboard.OPEN_TRIES)
    assert read_windows(held, no_sleep) is None
    assert held.closed == 0


def test_a_long_windows_clipboard_is_read_only_up_to_the_limit() -> None:
    calls = FakeWindowsClipboard("x" * (clipboard.MAX_CHARACTERS + 50))
    text = read_windows(calls, no_sleep)
    assert text is not None
    assert len(text) == clipboard.MAX_CHARACTERS


def test_macos_reads_pbpaste() -> None:
    commands: list[Sequence[str]] = []

    def run(command: Sequence[str]) -> bytes:
        commands.append(command)
        return TRACK.encode()

    assert read_mac(run) == TRACK
    assert commands == [["pbpaste"]]


def test_linux_uses_the_first_clipboard_tool_installed() -> None:
    commands: list[Sequence[str]] = []

    def run(command: Sequence[str]) -> bytes:
        commands.append(command)
        return b"\xff" + TRACK.encode()

    installed = {"xclip", "xsel"}
    text = read_linux(run, lambda name: name if name in installed else None)
    assert text == "\ufffd" + TRACK
    assert commands == [("xclip", "-selection", "clipboard", "-o")]


def test_linux_without_a_clipboard_tool_offers_nothing() -> None:
    assert read_linux(lambda command: b"", lambda name: None) is None


def test_a_clipboard_that_cant_be_read_gives_none_and_logs_no_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fails() -> str | None:
        raise subprocess.CalledProcessError(1, ["pbpaste"], output=TRACK.encode())

    monkeypatch.setitem(clipboard.READERS, clipboard.sys.platform, fails)
    with caplog.at_level(logging.DEBUG, logger=clipboard.__name__):
        assert read_clipboard() is None
    assert "CalledProcessError" in caplog.text
    assert "kaia" not in caplog.text
