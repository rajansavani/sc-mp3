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
        (None, None),
    ],
)
def test_the_save_date_drops_the_year_when_its_this_year(
    saved_at: datetime | None, shown: str | None
) -> None:
    entry = HistoryEntry(None, URL, Path("x.mp3"), "T", "A", "B", "mp3", saved_at=saved_at)
    assert cli_output.saved_on(entry) == shown


@pytest.mark.parametrize(
    ("saved_at", "message"),
    [
        (datetime(2024, 3, 9, 12, tzinfo=UTC), "Already in your library (saved Mar 9, 2024)."),
        (None, "Already in your library."),
    ],
)
def test_a_repeat_says_when_it_was_saved_when_the_history_knows(
    saved_at: datetime | None, message: str
) -> None:
    entry = HistoryEntry(None, URL, Path("x.mp3"), "T", "A", "B", "mp3", saved_at=saved_at)
    assert cli_output.already_saved(entry) == message


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


@pytest.mark.parametrize(
    ("reason", "start"),
    [
        ("private", "This video is private."),
        ("age_restricted", "YouTube shows this video only to signed-in adults"),
        ("members_only", "This video is only for the channel's members"),
        ("paid", "YouTube shows this video only to people who paid for it"),
        ("removed", "This video isn't available."),
        ("live_stream", "This is a live stream that hasn't ended."),
    ],
)
def test_a_video_youtube_wont_give_says_why(reason: ErrorReason, start: str) -> None:
    error = ScmpError(ErrorKind.unavailable, "refused", reason=reason)
    assert cli_output.error_message(error, Settings()).startswith(start)


@pytest.mark.parametrize("kind", [ErrorKind.rate_limited, ErrorKind.engine_outdated])
def test_messages_about_the_service_name_the_tracks_source(kind: ErrorKind) -> None:
    assert cli_output.error_message(ScmpError(kind), Settings()).startswith("SoundCloud ")
    youtube = cli_output.error_message(ScmpError(kind), Settings(), source="YouTube")
    assert youtube.startswith("YouTube ")


def test_every_reason_has_a_message() -> None:
    assert set(cli_output.REASON_MESSAGES) == set(get_args(ErrorReason))


@pytest.mark.parametrize(
    "kind", [ErrorKind.preview_only, ErrorKind.auth_expired, ErrorKind.engine_outdated]
)
def test_next_steps_the_command_line_cant_take_point_to_the_app(kind: ErrorKind) -> None:
    # The command line never signs in, and the engine is updated in the app's settings.
    assert "in the sc-mp3 app" in cli_output.error_message(ScmpError(kind), Settings())


@pytest.mark.parametrize(
    "error",
    [
        ScmpError(ErrorKind.drm),
        ScmpError(ErrorKind.unavailable, reason="private"),
        ScmpError(ErrorKind.unavailable, reason="age_restricted"),
        ScmpError(ErrorKind.unavailable, reason="members_only"),
        ScmpError(ErrorKind.unavailable, reason="paid"),
    ],
    ids=lambda error: error.reason or error.kind,
)
def test_a_song_no_one_can_save_from_this_upload_suggests_another(error: ScmpError) -> None:
    assert cli_output.error_message(error, Settings()).endswith("Try another upload of the song.")


def test_a_track_blocked_in_your_country_says_it_cant_be_saved_without_a_made_up_step() -> None:
    message = cli_output.error_message(ScmpError(ErrorKind.geo_blocked), Settings())
    assert message.endswith("so it can't be saved.")
    assert "Try " not in message


def test_a_file_that_isnt_audio_says_how_to_check_it() -> None:
    error = ScmpError(ErrorKind.convert_failed, reason="unreadable_file")
    message = cli_output.error_message(error, Settings())
    assert "Check that it plays in a music player." in message


def test_a_bot_check_says_to_wait_before_trying_again() -> None:
    message = cli_output.error_message(ScmpError(ErrorKind.bot_check), Settings())
    assert message.endswith("Wait a few minutes, then try again.")


def test_a_re_tag_that_cant_write_its_file_doesnt_offer_another_folder() -> None:
    error = ScmpError(ErrorKind.disk, "no space left", where="file")
    message = cli_output.error_message(error, Settings(library_dir=Path("/music")))
    assert message.startswith("sc-mp3 couldn't write the new tags into the file")
    assert "--out" not in message


def test_messages_that_need_the_log_name_it() -> None:
    message = cli_output.error_message(ScmpError(ErrorKind.internal), Settings())
    assert str(paths.log_dir() / LOG_FILE) in message


def test_missing_ffmpeg_on_linux_lists_each_distros_install_command() -> None:
    message = cli_output.ffmpeg_missing("linux")
    assert message.startswith("ffmpeg isn't installed. Install it, then run the command again.")
    assert "  Ubuntu and Debian: sudo apt install ffmpeg" in message
    assert "  Fedora: sudo dnf install ffmpeg-free" in message
    assert "  Arch: sudo pacman -S ffmpeg" in message
    assert "SCMP3_FFMPEG" in message


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_missing_ffmpeg_where_the_app_ships_it_says_to_reinstall(platform: str) -> None:
    message = cli_output.ffmpeg_missing(platform)
    assert message.startswith("sc-mp3 can't find its copy of ffmpeg. Reinstall sc-mp3")
    assert "apt" not in message


def test_a_job_without_ffmpeg_gets_the_missing_ffmpeg_message() -> None:
    error = ScmpError(ErrorKind.convert_failed, "not found", reason="ffmpeg_missing")
    assert cli_output.error_message(error, Settings()) == cli_output.FFMPEG_MISSING
