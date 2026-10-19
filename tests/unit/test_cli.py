"""Tests for the command table and the commands that a later ticket builds."""

import pytest

from scmp3 import cli, cli_output


def test_retag_is_not_implemented_yet(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["retag", "song.mp3"]) == cli_output.EXIT_ERROR
    assert capsys.readouterr().err.strip() == cli_output.NOT_IMPLEMENTED
