"""`scmp3 retag`: fix the tags of MP3 and M4A files you already have, after a review in the
terminal unless --yes is given.

Nothing is downloaded. Reviews and errors go to stderr, so stdout holds only the paths of the
re-tagged files.
"""

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from scmp3.cli_get import TITLE_STYLES, YES_ANSWERS, logging_to_file, set_up_streams
from scmp3.cli_output import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_STOPPED,
    EXIT_USAGE,
    INPUT_ENDED,
    LINKS_NEED_GET,
    NO_FILES,
    NOT_A_FILE,
    PLAYLIST_NOTE,
    PLAYLIST_QUESTION,
    RETAG_STOPPED,
    RETAGGED_NOTE,
    SKIPPED,
    error_message,
)
from scmp3.cli_review import (
    Describe,
    InputEnded,
    TrackReview,
    read_answer,
    review_lines,
    run_review,
    say,
)
from scmp3.core import pipeline
from scmp3.core.engine_start import log_start
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput, parse_inputs
from scmp3.core.models import ArtworkChoice, Settings, SourceInfo, Tags, TitleStyle
from scmp3.core.settings import SettingsStore
from scmp3.core.settings import default_path as default_settings_path
from scmp3.core.sources import localfile
from scmp3.core.urls import NOT_SUPPORTED

log = logging.getLogger(__name__)


def add_retag_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments for `scmp3 retag`."""
    parser.add_argument("files", nargs="+", metavar="file", help="an MP3 or M4A file")
    parser.add_argument(
        "--yes", "-y", action="store_true", help="write the suggested tags, without a review"
    )
    parser.add_argument(
        "--style", choices=TITLE_STYLES, help="the title style, instead of the one in your settings"
    )


@dataclass(frozen=True)
class RetagOptions:
    """What `scmp3 retag` was asked for, besides the files."""

    yes: bool
    style: TitleStyle | None


def run_retag(args: argparse.Namespace) -> int:
    """Re-tag each file, after a review unless --yes was given. Returns the exit code."""
    set_up_streams()
    # Separate arguments are joined with newlines, so a path with spaces stays whole.
    inputs = parse_inputs("\n".join(args.files))
    problem = _usage_problem(inputs)
    if problem is not None:
        say(problem)
        return EXIT_USAGE
    settings = SettingsStore(default_settings_path()).load()
    options = RetagOptions(yes=args.yes, style=args.style)
    tools = pipeline.Tools()
    with logging_to_file():
        log_start(args.engine_start)
        try:
            results = [_retag_one(parsed, options, settings, tools) for parsed in inputs]
        except InputEnded:
            say(INPUT_ENDED)
            return EXIT_ERROR
        except KeyboardInterrupt:
            say(RETAG_STOPPED)
            return EXIT_STOPPED
    return EXIT_OK if all(results) else EXIT_ERROR


def _usage_problem(inputs: list[ParsedInput]) -> str | None:
    """What's wrong with the arguments, as one message, or None when they're all files."""
    if not inputs:
        return NO_FILES
    rejects = [_why_not(parsed) for parsed in inputs if parsed.kind != "local_file"]
    return "\n".join(rejects) if rejects else None


def _why_not(parsed: ParsedInput) -> str:
    """Why an argument isn't a file to re-tag."""
    if parsed.url is not None:
        reason = LINKS_NEED_GET
    elif parsed.reason in (None, NOT_SUPPORTED):
        reason = NOT_A_FILE
    else:
        reason = parsed.reason
    # A paste that was too long has no text of its own to show.
    return f"{parsed.raw}: {reason}" if parsed.raw else reason


def _retag_one(
    parsed: ParsedInput, options: RetagOptions, settings: Settings, tools: pipeline.Tools
) -> bool:
    """Re-tag one file. Returns False when it failed, after saying why."""
    label = str(parsed.path)
    say(f"Reading {label}")
    try:
        prepared = pipeline.prepare_file(parsed, settings=settings, tools=tools)
    except ScmpError as error:
        _report(label, error, settings)
        return False
    try:
        return _review_and_write(prepared, options, settings)
    except ScmpError as error:
        _report(label, error, settings)
        return False
    finally:
        # retag deletes the job's folder; this covers skips and failures.
        pipeline.discard(prepared)


def _review_and_write(
    prepared: pipeline.Prepared, options: RetagOptions, settings: Settings
) -> bool:
    """Review the file's tags, then write them into it. Returns False when it failed."""
    source = prepared.source
    review = TrackReview(source, prepared.suggestion, settings, options.style)
    if options.yes:
        tags: Tags | None = review.tags()
    else:
        tags = run_review(review, retag_text(Path(source.id)), read_answer)
    if tags is None or not _may_rename(source, tags, options, settings):
        say(SKIPPED)
        return True
    saved = pipeline.retag(prepared, tags, ArtworkChoice(), settings)
    if options.yes and localfile.changes_identity(source, tags):
        say(PLAYLIST_NOTE)
    print(saved.path)
    say(RETAGGED_NOTE)
    return True


def _may_rename(source: SourceInfo, tags: Tags, options: RetagOptions, settings: Settings) -> bool:
    """Whether to write tags that change what Spotify's playlists know the song by. It's asked,
    unless --yes was given or the playlist warning is turned off in the settings."""
    if options.yes or not settings.retag_playlist_warning:
        return True
    if not localfile.changes_identity(source, tags):
        return True
    answer = read_answer(PLAYLIST_QUESTION).strip().lower()
    return answer == "" or answer in YES_ANSWERS


def retag_text(path: Path) -> Describe:
    """What the review of a file shows: its tags and where they're written."""
    has_cover = localfile.embedded_cover(path) is not None
    cover = (
        "the one in the file, kept as it is" if has_cover else "none, since the file has no cover"
    )

    def describe(review: TrackReview) -> str:
        lines = review_lines(review, review.tags())
        lines.append(f"Cover: {cover}")
        lines.append(f"Writes the new tags into {path}")
        return "\n".join(lines)

    return describe


def _report(label: str, error: ScmpError, settings: Settings) -> None:
    """Say why a file wasn't re-tagged, and log the details."""
    log.warning("Couldn't re-tag %s: %s", label, error, exc_info=error.kind == ErrorKind.internal)
    say(f"{label}: {error_message(error, settings)}")
