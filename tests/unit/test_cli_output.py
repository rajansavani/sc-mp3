"""Tests for what the command line prints: the error messages and the save dates."""

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from scmp3 import cli_output
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ErrorReason, ScmpError
from scmp3.core.log import LOG_FILE
from scmp3.core.models import HistoryEntry, Settings

URL = "https://soundcloud.com/kaia/midnight-drive"


@pytest.mark.parametrize(
    ("saved_at", "shown"),
    [
        (datetime(datetime.now().year, 10, 3, 12, tzinfo=UTC), "Oct 3"),
        (datetime(2024, 3, 9, 12, tzinfo=UTC), "Mar 9, 2024"),
        (None, "before"),
    ],
)
def test_the_save_date_drops_the_year_when_its_this_year(
    saved_at: datetime | None, shown: str
) -> None:
    entry = HistoryEntry(None, URL, Path("x.mp3"), "T", "A", "B", "mp3", saved_at=saved_at)
    assert cli_output.saved_on(entry) == shown


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_every_error_kind_has_a_message_with_nothing_left_to_fill_in(kind: ErrorKind) -> None:
    message = cli_output.error_message(ScmpError(kind), Settings(library_dir=Path("/music")))
    assert message
    assert "{" not in message


@pytest.mark.parametrize(
    ("reason", "start"),
    [
        ("file_in_use", "The file is open in another program"),
        ("path_too_long", "The folder path is too long"),
    ],
)
def test_a_disk_error_with_a_reason_gets_its_own_message(reason: ErrorReason, start: str) -> None:
    error = ScmpError(ErrorKind.disk, "can't write", where="library", reason=reason)
    message = cli_output.error_message(error, Settings(library_dir=Path("/music")))
    assert message.startswith(start)
    assert "/music" not in message


@pytest.mark.parametrize(
    ("commands", "listed"),
    [
        (["get"], "get"),
        (["get", "retag"], "get and retag"),
        (["get", "retag", "x"], "get, retag and x"),
    ],
)
def test_an_unknown_command_names_every_command(commands: list[str], listed: str) -> None:
    message = cli_output.unknown_command("gte", commands)
    assert message == f"Unknown command: gte. The commands are {listed}."


def test_every_reason_has_a_message() -> None:
    assert set(cli_output.REASON_MESSAGES) == set(get_args(ErrorReason))


def test_messages_that_need_the_log_name_it() -> None:
    message = cli_output.error_message(ScmpError(ErrorKind.internal), Settings())
    assert str(paths.log_dir() / LOG_FILE) in message
