"""Smoke tests for the package skeleton, its entry point and the shared audio fixtures."""

from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import pytest
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

import scmp3
from scmp3 import cli
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


def test_no_arguments_runs_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scmp3.app.main.run", lambda: 7)
    assert main([]) == 7


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["download", "x"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


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
