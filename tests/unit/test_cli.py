"""Tests for the command table: each command parses its own arguments and runs its own module."""

import pytest

from scmp3 import cli, cli_get, cli_retag


@pytest.mark.parametrize(
    ("argv", "runs"),
    [
        (["get", "https://soundcloud.com/kaia/song"], cli_get.run_get),
        (["retag", "a.mp3"], cli_retag.run_retag),
    ],
)
def test_each_command_runs_its_own_module(argv: list[str], runs: object) -> None:
    args = cli.build_parser().parse_args(argv)
    assert args.run is runs


def test_retag_takes_several_files_and_the_review_options() -> None:
    args = cli.build_parser().parse_args(
        ["retag", "a.mp3", "b.m4a", "--yes", "--style", "feat_in_title"]
    )
    assert args.files == ["a.mp3", "b.m4a"]
    assert args.yes is True
    assert args.style == "feat_in_title"
