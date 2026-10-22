"""Smoke tests for the package skeleton, its entry point and the shared audio fixtures."""

from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import pytest
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

import scmp3
from scmp3 import cli, cli_output, frozen
from scmp3.__main__ import main


def test_version_comes_from_package_metadata() -> None:
    assert scmp3.__version__ == version("sc-mp3")


def test_version_flag_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == scmp3.__version__


# The routing tests replace the CLI and the app, so they stay true once later tickets build them.
@pytest.mark.parametrize("argv", [["get", "x"], ["retag", "song.mp3"]])
def test_a_command_goes_to_the_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[Sequence[str]] = []

    def fake_cli_main(args: Sequence[str]) -> int:
        received.append(args)
        return 7

    monkeypatch.setattr(cli, "main", fake_cli_main)
    assert main(argv) == 7
    assert received == [argv]


class FakeApp:
    """Stands in for app.main.run, and records the files the app was opened with."""

    def __init__(self) -> None:
        self.opened_with: list[list[str]] = []

    def run(self, paths: Sequence[str] = ()) -> int:
        self.opened_with.append(list(paths))
        return 7


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FakeApp:
    fake = FakeApp()
    monkeypatch.setattr("scmp3.app.main.run", fake.run)
    return fake


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as if typed in a terminal, which pytest's captured stdout isn't."""
    monkeypatch.setattr("scmp3.__main__.in_terminal", lambda: True)


def test_no_arguments_runs_the_app(app: FakeApp) -> None:
    assert main([]) == 7
    assert app.opened_with == [[]]


def test_files_given_to_the_app_open_it_with_them(app: FakeApp, tmp_path: Path) -> None:
    # As when files are dropped on sc-mp3.exe, or it's chosen in Open with.
    songs = [tmp_path / "Night Bus.mp3", tmp_path / "Sunday.m4a"]
    for song in songs:
        song.write_bytes(b"")
    assert main([str(song) for song in songs]) == 7
    assert app.opened_with == [[str(song) for song in songs]]


@pytest.mark.usefixtures("terminal")
def test_a_file_typed_in_a_terminal_opens_the_app_too(app: FakeApp, tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"")
    assert main([str(song)]) == 7
    assert app.opened_with == [[str(song)]]


@pytest.mark.usefixtures("terminal")
def test_an_unknown_word_typed_in_a_terminal_names_the_commands(
    app: FakeApp, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["gte", "https://soundcloud.com/a/b"]) == cli_output.EXIT_USAGE
    assert capsys.readouterr().err == "Unknown command: gte. The commands are get and retag.\n"
    assert app.opened_with == []


def test_an_unknown_word_from_outside_a_terminal_opens_the_app(app: FakeApp) -> None:
    assert main(["gte"]) == 7
    assert app.opened_with == [["gte"]]


def test_unknown_options_and_finders_psn_argument_are_ignored(app: FakeApp) -> None:
    assert main(["--foo", "-psn_0_1234567"]) == 7
    assert app.opened_with == [[]]


@pytest.mark.parametrize("argv", [["--version", "--foo"], ["C:\\Music\\song.mp3", "--version"]])
def test_version_comes_first_whatever_else_is_given(
    argv: list[str], app: FakeApp, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 0
    assert capsys.readouterr().out.strip() == scmp3.__version__
    assert app.opened_with == []


def test_a_command_runs_after_the_frozen_apps_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frozen, "prepare", lambda: frozen.EXIT_FOLDER_TOO_LONG)
    monkeypatch.setattr(cli, "main", lambda args: pytest.fail("the command ran"))
    assert main(["get", "https://soundcloud.com/a/b"]) == frozen.EXIT_FOLDER_TOO_LONG


def test_help_lists_commands_from_the_cli_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = cli.Command(help="a made-up command", add_arguments=lambda p: None, run=lambda a: 0)
    monkeypatch.setitem(cli.COMMANDS, "madeup", extra)
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "Commands: get, retag, madeup." in capsys.readouterr().out


def test_tiny_mp3_is_one_second_of_mp3(tiny_mp3: Path) -> None:
    audio = MP3(tiny_mp3)
    assert audio.info.length == pytest.approx(1.0, abs=0.1)
    # Mutagen works the bitrate out from the file size, so it can be off by a bit per second.
    assert audio.info.bitrate == pytest.approx(320_000, rel=0.01)


def test_tiny_m4a_is_one_second_of_aac(tiny_m4a: Path) -> None:
    audio = MP4(tiny_m4a)
    assert audio.info.length == pytest.approx(1.0, abs=0.1)
    assert audio.info.codec == "mp4a.40.2"
