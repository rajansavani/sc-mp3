"""`scmp3 get`: save SoundCloud and YouTube links, after a review in the terminal unless --yes
is given.

Reviews, progress and errors go to stderr, so stdout holds only the paths of the saved files.
"""

import argparse
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TextIO, get_args

from scmp3.cli_output import (
    COVER_NOT_DOWNLOADED,
    COVER_PROBLEMS,
    ERROR_MESSAGES,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_STOPPED,
    EXIT_USAGE,
    FFMPEG_MISSING,
    INPUT_ENDED,
    NO_LOG,
    NOTHING_TO_SAVE,
    SAVE_AGAIN_WITH_REVIEW,
    SKIPPED,
    SKIPPED_NAME_TAKEN,
    STOPPED,
    TITLE_NEEDS_ONE_LINK,
    error_message,
    saved_on,
)
from scmp3.cli_review import (
    WHOLE_NUMBER,
    InputEnded,
    TrackReview,
    ask_about_collision,
    read_answer,
    run_review,
    say,
)
from scmp3.core import paths, pipeline
from scmp3.core.engine_start import log_start
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput, parse_inputs
from scmp3.core.log import close_logging, setup_logging
from scmp3.core.models import ArtworkChoice, HistoryEntry, Progress, Settings, Tags, TitleStyle
from scmp3.core.settings import SettingsStore
from scmp3.core.settings import default_path as default_settings_path
from scmp3.core.urls import UnsupportedInput

# The flags that set a field by hand. Each is named after the tag field it sets.
FIELD_FLAGS = ("title", "artist", "album", "year", "genre")
TITLE_STYLES: tuple[TitleStyle, ...] = get_args(TitleStyle)
YES_ANSWERS = frozenset({"y", "yes"})
# How messages name each source.
SOURCE_NAMES = {"soundcloud": "SoundCloud", "youtube": "YouTube"}

log = logging.getLogger(__name__)


def add_get_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments for `scmp3 get`."""
    parser.add_argument(
        "urls", nargs="+", metavar="url", help="a SoundCloud track link or a YouTube video link"
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="save with the suggested tags, without a review"
    )
    parser.add_argument(
        "--style", choices=TITLE_STYLES, help="the title style, instead of the one in your settings"
    )
    parser.add_argument("--title", type=_filled_in, help="use this title")
    parser.add_argument("--artist", type=_filled_in, help="use this artist")
    parser.add_argument("--album", help="use this album")
    parser.add_argument("--year", type=_year, help="use this year")
    parser.add_argument("--genre", help="use this genre")
    parser.add_argument(
        "--out", type=Path, metavar="DIR", help="save here, instead of the folder in your settings"
    )


def _filled_in(text: str) -> str:
    if not text.strip():
        raise argparse.ArgumentTypeError("it can't be empty")
    return text.strip()


def _year(text: str) -> str:
    text = text.strip()
    if WHOLE_NUMBER.fullmatch(text) and int(text) in pipeline.NUMBER_RANGES["year"]:
        return text
    raise argparse.ArgumentTypeError("the year must be a number from 1 to 9999")


@dataclass(frozen=True)
class GetOptions:
    """What `scmp3 get` was asked for, besides the links."""

    yes: bool
    style: TitleStyle | None
    overrides: dict[str, str]  # the fields set by flags, by tag field

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "GetOptions":
        flags = {name: getattr(args, name) for name in FIELD_FLAGS}
        overrides = {name: value for name, value in flags.items() if value is not None}
        return cls(yes=args.yes, style=args.style, overrides=overrides)


def run_get(args: argparse.Namespace) -> int:
    """Save each link, after a review unless --yes was given. Returns the exit code."""
    _set_up_streams()
    # Separate arguments are joined with newlines, so a quoted path with spaces stays whole.
    inputs = parse_inputs("\n".join(args.urls))
    problem = _usage_problem(inputs, args)
    if problem is not None:
        say(problem)
        return EXIT_USAGE
    try:
        paths.ffmpeg_exe()
    except ScmpError:
        say(FFMPEG_MISSING)
        return EXIT_ERROR
    settings = _load_settings(args.out)
    options = GetOptions.from_args(args)
    tools = pipeline.Tools()
    with _logging_to_file():
        log_start(args.engine_start)
        try:
            results = [_get_one(parsed, options, settings, tools) for parsed in inputs]
        except InputEnded:
            say(INPUT_ENDED)
            return EXIT_ERROR
        except KeyboardInterrupt:
            say(STOPPED)
            return EXIT_STOPPED
    return EXIT_OK if all(results) else EXIT_ERROR


def _usage_problem(inputs: list[ParsedInput], args: argparse.Namespace) -> str | None:
    """What's wrong with the arguments, as one message, or None when they're fine."""
    if not inputs:
        return NOTHING_TO_SAVE
    rejects = []
    for parsed in inputs:
        reason = pipeline.unsupported_reason(parsed)
        if reason is not None:
            # A paste that was too long has no text of its own to show.
            rejects.append(f"{parsed.raw}: {reason}" if parsed.raw else reason)
    if rejects:
        return "\n".join(rejects)
    if args.title is not None and len(inputs) > 1:
        return TITLE_NEEDS_ONE_LINK
    return None


def _load_settings(out: Path | None) -> Settings:
    """The saved settings, with --out as the library folder when it's given."""
    settings = SettingsStore(default_settings_path()).load()
    if out is None:
        return settings
    return replace(settings, library_dir=Path(os.path.abspath(out.expanduser())))


def _get_one(
    parsed: ParsedInput, options: GetOptions, settings: Settings, tools: pipeline.Tools
) -> bool:
    """Save one link. Returns False when it failed, after saying why."""
    label = parsed.url or parsed.raw
    say(f"Fetching {label}")
    try:
        prepared = pipeline.prepare(parsed, settings=settings, tools=tools)
    except UnsupportedInput as error:
        say(f"{label}: {error}")
        return False
    except ScmpError as error:
        _report(label, error, settings, _source_name(parsed))
        return False
    try:
        return _save(prepared, options, settings)
    except ScmpError as error:
        _report(label, error, settings, _source_name(parsed))
        return False
    finally:
        # finalize deletes the folder after a save; this covers skips and failures.
        pipeline.discard(prepared)


def _save(prepared: pipeline.Prepared, options: GetOptions, settings: Settings) -> bool:
    """Download, review and save a prepared track. Returns False when it failed."""
    if prepared.duplicate is not None and not _save_again(prepared.duplicate, options):
        # Under --yes a repeat counts as a failure, so a script can tell it wasn't saved.
        return not options.yes
    staged = _fetch(prepared)
    review = TrackReview(prepared.source, prepared.suggestion, settings, options.style)
    review.overrides.update(options.overrides)
    tags: Tags | None = review.tags() if options.yes else run_review(review, staged, read_answer)
    if tags is None:
        say(SKIPPED)
        return True
    saved = pipeline.finalize(
        prepared,
        staged,
        tags,
        ArtworkChoice(),
        _collision_settings(settings, options),
        style=options.style,
        suggestion=review.suggestion,
        overrides=review.overrides,
        ask=None if options.yes else ask_about_collision(read_answer),
    )
    if saved is None:
        say(SKIPPED_NAME_TAKEN)
        return True
    if saved.cover_error is not None:
        say(COVER_PROBLEMS.get(saved.cover_error.kind, COVER_NOT_DOWNLOADED))
    print(saved.path)
    return True


def _save_again(duplicate: HistoryEntry, options: GetOptions) -> bool:
    """Whether to save a track that's in the history again. With --yes, it never is."""
    already = ERROR_MESSAGES[ErrorKind.duplicate].format(saved=saved_on(duplicate))
    if options.yes:
        say(f"{already} {SAVE_AGAIN_WITH_REVIEW}")
        return False
    answer = read_answer(f"{already} It's at {duplicate.saved_path}. Save it again? [y/N] ")
    if answer.strip().lower() in YES_ANSWERS:
        return True
    say(SKIPPED)
    return False


def _collision_settings(settings: Settings, options: GetOptions) -> Settings:
    """The settings to save with. With --yes there's nobody to ask, so both files are kept."""
    if options.yes and settings.when_file_exists == "ask":
        return replace(settings, when_file_exists="keep_both")
    return settings


def _fetch(prepared: pipeline.Prepared) -> pipeline.Staged:
    """Download and convert, showing the progress on a terminal."""
    if not sys.stderr.isatty():
        say("Downloading")
        return pipeline.fetch(prepared)
    progress = ProgressLine(sys.stderr)
    try:
        return pipeline.fetch(prepared, on_progress=progress)
    finally:
        progress.end()


class ProgressLine:
    """Shows a download's percentage on one terminal line."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._shown: int | None = None

    def __call__(self, progress: Progress) -> None:
        if progress.pct is None or int(progress.pct) == self._shown:
            return
        self._shown = int(progress.pct)
        print(f"\rDownloading {self._shown}%", end="", file=self._stream, flush=True)

    def end(self) -> None:
        """Finish the line, if anything was shown."""
        if self._shown is not None:
            print(file=self._stream)


def _source_name(parsed: ParsedInput) -> str:
    """How messages name the source a link comes from."""
    source = pipeline.LINK_SOURCES.get(parsed.kind, "soundcloud")
    return SOURCE_NAMES[source]


def _report(label: str, error: ScmpError, settings: Settings, source: str) -> None:
    """Say why a link failed, and log the details."""
    log.warning("Couldn't save %s: %s", label, error, exc_info=error.kind == ErrorKind.internal)
    say(f"{label}: {error_message(error, settings, source=source)}")


def _set_up_streams() -> None:
    """Use UTF-8 for pipes and files, so piped answers and printed paths come through exactly.

    A terminal keeps its own encoding, and shows "?" for a character it can't, instead of failing
    the save.
    """
    # utf-8-sig drops the byte order mark Windows PowerShell can put in front of piped text.
    _set_up_stream(sys.stdin, piped_encoding="utf-8-sig")
    _set_up_stream(sys.stdout, piped_encoding="utf-8")
    _set_up_stream(sys.stderr, piped_encoding="utf-8")


def _set_up_stream(stream: TextIO | None, *, piped_encoding: str) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if stream is None or reconfigure is None:
        return
    if stream.isatty():
        reconfigure(errors="replace")
        return
    reconfigure(encoding=piped_encoding, errors="replace")


@contextmanager
def _logging_to_file() -> Iterator[None]:
    """Log to the app's log file while a command runs, carrying on without it if it can't."""
    try:
        setup_logging()
    except ScmpError as error:
        say(NO_LOG.format(detail=error.detail))
    try:
        yield
    finally:
        close_logging()
