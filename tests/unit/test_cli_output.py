"""Tests for what the command line prints: the error messages and the save dates."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scmp3 import cli_output
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
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


def test_messages_that_need_the_log_name_it() -> None:
    message = cli_output.error_message(ScmpError(ErrorKind.internal), Settings())
    assert str(paths.log_dir() / LOG_FILE) in message
