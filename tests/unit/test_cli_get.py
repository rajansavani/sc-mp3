"""Tests for `scmp3 get`, with the pipeline's stages replaced by a fake."""

import io
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scmp3 import cli, cli_get, cli_output
from scmp3.core import paths, pipeline
from scmp3.core.convert import AudioInfo
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.log import FILE_HANDLER, LOG_FILE
from scmp3.core.models import (
    ArtworkChoice,
    FormatOption,
    HistoryEntry,
    Progress,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
)
from scmp3.core.suggest import suggest
from scmp3.core.urls import UnsupportedInput

URL = "https://soundcloud.com/kaia/midnight-drive"
OTHER_URL = "https://soundcloud.com/kaia/paper-moon"


def source_for(url: str) -> SourceInfo:
    name = url.rsplit("/", 1)[1].replace("-", " ").title()
    return SourceInfo(
        "soundcloud", f"soundcloud:tracks:{len(url)}", url, f"{name} [prod. Hazy] FREE DL", "Kaia"
    )


@dataclass
class Finalized:
    """What finalize was given."""

    tags: Tags
    cover: ArtworkChoice
    settings: Settings
    style: str | None
    suggestion: TagSuggestion | None
    overrides: Mapping[str, str] | None
    ask: Any


@dataclass
class FakePipeline:
    """Stands in for prepare, fetch, finalize and discard, keeping what each was given."""

    library: Path
    errors: dict[str, BaseException] = field(default_factory=dict)  # by URL, raised by prepare
    fetch_error: BaseException | None = None
    duplicate: HistoryEntry | None = None
    skip_on_save: bool = False
    cover_error: ScmpError | None = None  # why finalize left the cover out
    place_error: ScmpError | None = None  # raised by finalize, as by a failed move
    title: str | None = None  # replaces every source's title
    prepared: list[pipeline.Prepared] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    finalized: list[Finalized] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def prepare(self, parsed: ParsedInput, *, settings: Settings, **kwargs: Any) -> Any:
        assert parsed.url is not None
        if parsed.url in self.errors:
            raise self.errors[parsed.url]
        source = source_for(parsed.url)
        if self.title is not None:
            source.title = self.title
        prepared = pipeline.Prepared(
            job_id=f"job{len(self.prepared)}",
            source=source,
            suggestion=suggest(source, settings),
            duplicate=self.duplicate,
            settings=settings,
            info={},
        )
        self.prepared.append(prepared)
        return prepared

    def fetch(self, prepared: pipeline.Prepared, **kwargs: Any) -> pipeline.Staged:
        self.fetched.append(prepared.job_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        audio = AudioInfo(duration=200.0, bitrate=320, codec="mp3", sample_rate=44100)
        downloaded = FormatOption("hls_aac_160k", "m4a", codec="aac", bitrate=160.0)
        return pipeline.Staged(Path("audio.mp3"), "mp3", audio, downloaded)

    def finalize(
        self,
        prepared: pipeline.Prepared,
        staged: pipeline.Staged,
        tags: Tags,
        cover: ArtworkChoice,
        settings: Settings,
        *,
        style: Any = None,
        suggestion: TagSuggestion | None = None,
        overrides: Mapping[str, str] | None = None,
        ask: Any = None,
    ) -> pipeline.Saved | None:
        self.finalized.append(Finalized(tags, cover, settings, style, suggestion, overrides, ask))
        if self.place_error is not None:
            raise self.place_error
        if self.skip_on_save:
            return None
        reviewed = suggestion or prepared.suggestion
        dest = pipeline.destination(
            prepared.source, reviewed, staged, tags, settings, style=style, overrides=overrides
        )
        # Like resolve_collision, which asks only when the name is taken.
        if ask is not None and dest.exists():
            ask(dest)
        return pipeline.Saved(dest, self.cover_error)

    def discard(self, prepared: pipeline.Prepared) -> None:
        self.discarded.append(prepared.job_id)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePipeline:
    fake = FakePipeline(tmp_path / "out")
    for name in ("prepare", "fetch", "finalize", "discard"):
        monkeypatch.setattr(pipeline, name, getattr(fake, name))
    # The CLI checks for ffmpeg first; these tests never run it.
    monkeypatch.setattr(paths, "ffmpeg_exe", lambda: Path("ffmpeg"))
    return fake


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answers for input(), in order. A test appends to it."""
    lines: list[str] = []
    replies: Iterator[str] = iter(lines)

    def fake_input() -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    return lines


def get(*args: str, out: Path | None = None) -> int:
    argv = ["get", *args]
    if out is not None:
        argv += ["--out", str(out)]
    return cli.main(argv)


# Saving


def test_yes_saves_with_the_suggested_tags_and_prints_only_the_path(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    [saved] = fake.finalized
    assert (saved.tags.title, saved.tags.artist) == ("Midnight Drive", "Kaia")
    assert saved.cover == ArtworkChoice()
    assert saved.settings.library_dir == fake.library
    shown = capsys.readouterr()
    assert shown.out == f"{fake.library / 'Kaia - Midnight Drive.mp3'}\n"
    assert f"Fetching {URL}" in shown.err


def test_finalize_gets_what_the_tags_were_rendered_from(
    fake: FakePipeline, typed: list[str]
) -> None:
    typed.extend(["album Nights", "1", ""])
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    [saved] = fake.finalized
    assert saved.overrides == {"album": "Nights"}
    assert saved.suggestion is not None
    # Change 1 was turned off in the review, and finalize gets the suggestion as it was left.
    assert saved.suggestion.changes[0].enabled is False


def test_flags_set_fields_and_the_style(fake: FakePipeline) -> None:
    flags = ["--title", "Night Drive", "--artist", "Kaia & Lou V", "--album", "Nights"]
    flags += ["--year", "2024", "--genre", "House", "--style", "all_in_title"]
    assert get("--yes", *flags, URL, out=fake.library) == cli_output.EXIT_OK
    [saved] = fake.finalized
    tags = saved.tags
    assert (tags.title, tags.artist, tags.album) == ("Night Drive", "Kaia & Lou V", "Nights")
    assert (tags.year, tags.genre) == (2024, "House")
    assert saved.style == "all_in_title"


def test_a_relative_out_folder_becomes_absolute(
    fake: FakePipeline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert get("--yes", URL, "--out", "songs") == cli_output.EXIT_OK
    assert fake.finalized[0].settings.library_dir == tmp_path / "songs"


def test_without_out_the_library_folder_comes_from_the_settings(fake: FakePipeline) -> None:
    assert get("--yes", URL) == cli_output.EXIT_OK
    assert fake.finalized[0].settings.library_dir == Settings().library_dir


def test_with_yes_a_taken_name_keeps_both_files_instead_of_asking(fake: FakePipeline) -> None:
    get("--yes", URL, out=fake.library)
    [saved] = fake.finalized
    assert saved.settings.when_file_exists == "keep_both"
    assert saved.ask is None


def test_the_review_asks_about_a_taken_name(fake: FakePipeline, typed: list[str]) -> None:
    typed.append("")
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    [saved] = fake.finalized
    assert saved.settings.when_file_exists == "ask"
    assert callable(saved.ask)


def test_a_skipped_name_is_not_an_error(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.skip_on_save = True
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    shown = capsys.readouterr()
    assert shown.out == ""
    assert cli_output.SKIPPED_NAME_TAKEN in shown.err


def test_each_link_is_saved_in_turn_and_the_staging_folder_cleared(fake: FakePipeline) -> None:
    assert get("--yes", URL, OTHER_URL, URL, out=fake.library) == cli_output.EXIT_OK
    # The repeated link is dropped.
    assert [p.source.url for p in fake.prepared] == [URL, OTHER_URL]
    assert fake.discarded == ["job0", "job1"]


# The review


def test_the_review_saves_what_you_changed(
    fake: FakePipeline, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    typed.extend(["title Night Drive", "year 2024", ""])
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    tags = fake.finalized[0].tags
    assert (tags.title, tags.year) == ("Night Drive", 2024)
    shown = capsys.readouterr()
    assert "Original title: Midnight Drive [prod. Hazy] FREE DL" in shown.err
    assert shown.out.strip().endswith("Kaia - Night Drive.mp3")


def test_q_skips_the_track_without_saving(
    fake: FakePipeline, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    typed.append("q")
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    assert fake.finalized == []
    assert fake.discarded == ["job0"]
    assert cli_output.SKIPPED in capsys.readouterr().err


def test_input_that_ends_mid_review_stops_with_an_error(
    fake: FakePipeline, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert get(URL, OTHER_URL, out=fake.library) == cli_output.EXIT_ERROR
    assert fake.finalized == []
    assert len(fake.prepared) == 1
    assert fake.discarded == ["job0"]
    assert cli_output.INPUT_ENDED in capsys.readouterr().err


def test_ctrl_c_stops_and_clears_the_staging_folder(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.fetch_error = KeyboardInterrupt()
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_STOPPED
    assert fake.discarded == ["job0"]
    assert cli_output.STOPPED in capsys.readouterr().err


# Duplicates


def earlier_save() -> HistoryEntry:
    when = datetime(2026, 10, 3, 12, tzinfo=UTC)
    return HistoryEntry(
        None, URL, Path("Kaia - Midnight Drive.mp3"), "T", "A", "B", "mp3", saved_at=when
    )


def test_with_yes_a_track_saved_before_is_not_saved_again(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.duplicate = earlier_save()
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_ERROR
    assert fake.fetched == []
    assert fake.discarded == ["job0"]
    err = capsys.readouterr().err
    assert "Already in your library (saved Oct 3" in err
    assert cli_output.SAVE_AGAIN_WITH_REVIEW in err


def test_the_review_asks_before_saving_a_track_again(fake: FakePipeline, typed: list[str]) -> None:
    fake.duplicate = earlier_save()
    typed.extend(["y", ""])
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    assert len(fake.finalized) == 1


def test_saying_no_to_saving_again_skips_the_download(fake: FakePipeline, typed: list[str]) -> None:
    fake.duplicate = earlier_save()
    typed.append("")
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    assert fake.fetched == []


# Failures


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (ErrorKind.drm, cli_output.ERROR_MESSAGES[ErrorKind.drm]),
        (ErrorKind.preview_only, "Only a 30-second preview is available: this is a Go+ track."),
        (ErrorKind.offline, "You're offline."),
    ],
)
def test_a_failure_prints_the_message_for_its_kind(
    kind: ErrorKind, message: str, fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.errors[URL] = ScmpError(kind, "details for the log")
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_ERROR
    shown = capsys.readouterr()
    assert shown.out == ""
    assert f"{URL}: {message}" in shown.err
    assert "details for the log" not in shown.err


def test_one_failure_doesnt_stop_the_other_links(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.errors[URL] = ScmpError(ErrorKind.drm)
    assert get("--yes", URL, OTHER_URL, out=fake.library) == cli_output.EXIT_ERROR
    assert capsys.readouterr().out.strip().endswith("Kaia - Paper Moon.mp3")


def test_a_failed_download_clears_the_staging_folder(fake: FakePipeline) -> None:
    fake.fetch_error = ScmpError(ErrorKind.convert_failed, "ffmpeg exited with 1")
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_ERROR
    assert fake.discarded == ["job0"]


def test_a_short_link_to_a_set_says_why_it_cant_be_saved(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    short = "https://on.soundcloud.com/AbCdEfGh"
    fake.errors[short] = UnsupportedInput(pipeline.SETS_NOT_SUPPORTED)
    assert get("--yes", short, out=fake.library) == cli_output.EXIT_ERROR
    assert f"{short}: {pipeline.SETS_NOT_SUPPORTED}" in capsys.readouterr().err


def test_the_details_go_to_the_log_file(fake: FakePipeline) -> None:
    fake.errors[URL] = ScmpError(ErrorKind.engine_outdated, "HTTP 403 from api-v2.soundcloud.com")
    get("--yes", URL, out=fake.library)
    text = (paths.log_dir() / LOG_FILE).read_text(encoding="utf-8")
    assert "HTTP 403 from api-v2.soundcloud.com" in text
    # The command closes its log handler when it ends.
    assert FILE_HANDLER not in [handler.get_name() for handler in logging.getLogger().handlers]


def test_a_log_that_cant_be_written_doesnt_stop_the_command(
    fake: FakePipeline, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_log() -> Path:
        raise ScmpError(ErrorKind.disk, "can't open the log file: Permission denied")

    monkeypatch.setattr(cli_get, "setup_logging", no_log)
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    assert "The log file can't be written" in capsys.readouterr().err


def test_missing_ffmpeg_stops_before_anything_is_fetched(
    fake: FakePipeline, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def not_found() -> Path:
        raise ScmpError(ErrorKind.convert_failed, "ffmpeg not found")

    monkeypatch.setattr(paths, "ffmpeg_exe", not_found)
    assert get("--yes", URL) == cli_output.EXIT_ERROR
    assert fake.prepared == []
    assert cli_output.FFMPEG_MISSING in capsys.readouterr().err


# Usage errors


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["https://example.com/song"], "https://example.com/song: Not a supported link."),
        (["https://soundcloud.com/kaia/sets/nights"], pipeline.SETS_NOT_SUPPORTED),
        (["https://youtu.be/dQw4w9WgXcQ"], pipeline.YOUTUBE_NOT_SUPPORTED),
        (["   "], cli_output.NOTHING_TO_SAVE),
        (["--title", "Same", URL, OTHER_URL], cli_output.TITLE_NEEDS_ONE_LINK),
    ],
)
def test_arguments_it_cant_use_are_a_usage_error(
    args: list[str], said: str, fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    assert get("--yes", *args) == cli_output.EXIT_USAGE
    assert fake.prepared == []
    assert said in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--year", "0", URL],
        ["--year", "next", URL],
        ["--title", " ", URL],
        ["--style", "loud", URL],
    ],
)
def test_bad_flag_values_are_a_usage_error(args: list[str], fake: FakePipeline) -> None:
    with pytest.raises(SystemExit) as raised:
        get(*args)
    assert raised.value.code == cli_output.EXIT_USAGE


# Output


def test_the_progress_line_shows_each_new_percentage_once() -> None:
    stream = io.StringIO()
    line = cli_get.ProgressLine(stream)
    for pct in (None, 10.2, 10.9, 55.0, 100.0):
        line(Progress(pct=pct))
    line.end()
    assert stream.getvalue() == "\rDownloading 10%\rDownloading 55%\rDownloading 100%\n"


class Terminal(io.TextIOWrapper):
    """A text stream that says it's a terminal."""

    def isatty(self) -> bool:
        return True


def cp1252(data: bytes = b"", *, terminal: bool = False) -> io.TextIOWrapper:
    """A stream in the Windows code page, which is what Python gives pipes on Windows."""
    kind = Terminal if terminal else io.TextIOWrapper
    return kind(io.BytesIO(data), encoding="cp1252", newline="\n")


def test_a_redirected_stdout_gets_the_saved_path_in_utf8(
    fake: FakePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = cp1252()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", cp1252())
    fake.title = "夜 Drive"
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    out.flush()
    expected = f"{fake.library / 'Kaia - 夜 Drive.mp3'}\n".encode()
    assert out.buffer.getvalue() == expected


def test_a_terminal_keeps_its_encoding_and_shows_what_it_cant_as_a_question_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = cp1252(terminal=True)
    monkeypatch.setattr("sys.stdout", terminal)
    cli_get._set_up_streams()
    print("Kaia → 夜", file=terminal)
    terminal.flush()
    assert terminal.encoding == "cp1252"
    assert terminal.buffer.getvalue() == b"Kaia ? ?\n"


def test_piped_answers_are_read_as_utf8_without_the_byte_order_mark(
    fake: FakePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What Windows PowerShell sends with a UTF-8 console: a BOM, then UTF-8 text.
    answers = "\ufeffalbum Café\n\n".encode()
    monkeypatch.setattr("sys.stdin", cp1252(answers))
    assert get(URL, out=fake.library) == cli_output.EXIT_OK
    assert fake.finalized[0].tags.album == "Café"


# Covers and folders


def test_a_cover_left_out_is_one_warning_line_and_the_file_still_saves(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.cover_error = ScmpError(ErrorKind.unavailable, "HTTP 404 from i1.sndcdn.com")
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    shown = capsys.readouterr()
    assert cli_output.COVER_NOT_DOWNLOADED in shown.err
    assert shown.out == f"{fake.library / 'Kaia - Midnight Drive.mp3'}\n"


def test_a_cover_image_that_cant_be_read_says_so(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.cover_error = ScmpError(ErrorKind.convert_failed, "cannot identify image file")
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_OK
    assert cli_output.COVER_PROBLEMS[ErrorKind.convert_failed] in capsys.readouterr().err


def test_a_disk_error_in_sc_mp3s_own_folders_doesnt_point_at_out(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.fetch_error = ScmpError(ErrorKind.disk, "No space left on device", where="data")
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_ERROR
    err = capsys.readouterr().err
    assert cli_output.PLACE_MESSAGES[(ErrorKind.disk, "data")] in err
    assert "--out" not in err


@pytest.mark.parametrize("where", [None, "library"])
def test_a_disk_error_in_the_library_suggests_another_folder(
    where: Any, fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.place_error = ScmpError(ErrorKind.disk, "Access is denied", where=where)
    assert get("--yes", URL, out=fake.library) == cli_output.EXIT_ERROR
    assert f"Can't save to {fake.library}. Choose another folder with --out." in (
        capsys.readouterr().err
    )


def test_input_that_ends_at_the_taken_name_question_stops_with_an_error(
    fake: FakePipeline, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    taken = fake.library / "Kaia - Midnight Drive.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"old")
    # Enter saves the review, then the input ends at the question about the taken name.
    typed.append("")
    assert get(URL, OTHER_URL, out=fake.library) == cli_output.EXIT_ERROR
    err = capsys.readouterr().err
    assert cli_output.INPUT_ENDED in err
    assert "didn't expect" not in err
    assert len(fake.prepared) == 1
