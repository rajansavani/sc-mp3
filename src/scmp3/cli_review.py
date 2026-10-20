"""The review `scmp3 get` shows in the terminal, and the questions it asks there.

Everything goes to stderr, so stdout keeps only the paths of the saved files.
"""

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from scmp3.core import pipeline
from scmp3.core.library import AskFn, CollisionChoice
from scmp3.core.models import Settings, SourceInfo, Tags, TagSuggestion, TitleStyle
from scmp3.core.suggest import InvalidOverride, apply_changes

REVIEW_HELP = (
    "Press Enter to save, or type q to skip. Type a number to turn that change on or off. "
    'To change a field, type its name and the new value, like "year 2024". '
    "A name on its own clears the field."
)

# The fields the review can change, by the name you type, with the tag field each one sets.
EDITABLE_FIELDS: dict[str, str] = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album artist": "album_artist",
    "year": "year",
    "genre": "genre",
    "track": "track_no",
}
# Longest first, so "album artist Kaia" sets the album artist rather than the album.
FIELD_NAMES_LONGEST_FIRST = sorted(EDITABLE_FIELDS, key=len, reverse=True)
REQUIRED_FIELDS = frozenset({"title", "artist"})
# What to say when a typed number can't be used, by the tag field.
FIELD_PROBLEMS: dict[str, str] = {
    "year": "The year must be a number from 1 to 9999.",
    "track_no": "The track must be a number from 1 to 999, or a number and a total, like 3/12.",
    "track_total": "The total must be from 1 to 999, and at least the track number.",
}

SAVE_ANSWERS = frozenset({"", "save"})
SKIP_ANSWERS = frozenset({"q", "skip"})
COLLISION_ANSWERS: dict[str, CollisionChoice] = {
    "r": "replace",
    "k": "keep_both",
    "s": "skip",
}
WHOLE_NUMBER = re.compile(r"[0-9]+")

# Asks a question and returns the answer, like `read_answer`. Tests pass their own.
Reader = Callable[[str], str]


class InputEnded(Exception):
    """The input closed before an answer came, as when stdin isn't a terminal."""


def say(text: str) -> None:
    """Show a line on stderr."""
    print(text, file=sys.stderr)


def read_answer(prompt: str) -> str:
    """Ask on stderr and read a line. Raises InputEnded when the input has closed."""
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        line = input()
    except EOFError as error:
        raise InputEnded from error
    # Windows PowerShell 5.1 starts the text it pipes in with a byte order mark.
    return line.removeprefix("﻿")


@dataclass
class TrackReview:
    """One track's review: the suggestion with its changes on or off, and the hand edits."""

    source: SourceInfo
    suggestion: TagSuggestion
    settings: Settings
    style: TitleStyle | None
    overrides: dict[str, str] = field(default_factory=dict)  # by tag field

    def tags(self) -> Tags:
        """The tags as the review stands."""
        return pipeline.render_tags(
            self.source, self.suggestion, self.settings, style=self.style, overrides=self.overrides
        )

    def answer(self, text: str) -> str | None:
        """Act on a change number or a field edit. Returns a problem to show, or None."""
        text = text.strip()
        word = text.partition(" ")[0]
        if WHOLE_NUMBER.fullmatch(word):
            return self.toggle(int(word))
        edit = field_edit(text)
        if edit is not None:
            return self.edit(*edit)
        return REVIEW_HELP

    def toggle(self, number: int) -> str | None:
        """Turn the numbered change on or off, and redo the title and artists."""
        changes = list(self.suggestion.changes)
        if not 1 <= number <= len(changes):
            return f"There's no change {number}."
        change = changes[number - 1]
        changes[number - 1] = replace(change, enabled=not change.enabled)
        toggled = replace(self.suggestion, changes=changes)
        self.suggestion = apply_changes(toggled, self.source, self.settings)
        return None

    def edit(self, name: str, value: str) -> str | None:
        """Set a field by hand, or clear it when `value` is empty."""
        if not value and name in REQUIRED_FIELDS:
            return f"The {name} can't be empty."
        before = dict(self.overrides)
        self.overrides[EDITABLE_FIELDS[name]] = value
        try:
            self.tags()
        except InvalidOverride as error:
            self.overrides = before
            return FIELD_PROBLEMS[error.field]
        return None


def field_edit(text: str) -> tuple[str, str] | None:
    """The field a typed edit names and its new value, or None when it names no field."""
    for name in FIELD_NAMES_LONGEST_FIRST:
        head, rest = text[: len(name)], text[len(name) :]
        if head.lower() == name and (not rest or rest.startswith(" ")):
            return name, rest.strip()
    return None


def run_review(review: TrackReview, staged: pipeline.Staged, read: Reader) -> Tags | None:
    """Show the review until you save or skip. Returns the tags to save, or None to skip."""
    say(REVIEW_HELP)
    while True:
        say(review_text(review, staged))
        answer = read("> ").strip()
        if answer.lower() in SAVE_ANSWERS:
            return review.tags()
        if answer.lower() in SKIP_ANSWERS:
            return None
        problem = review.answer(answer)
        if problem is not None:
            say(problem)


def review_text(review: TrackReview, staged: pipeline.Staged) -> str:
    """The original title, the fields, the numbered changes and where the file goes."""
    tags = review.tags()
    fields = {
        "Title": tags.title,
        "Artist": tags.artist,
        "Album": tags.album,
        "Album artist": tags.album_artist,
        "Year": tags.year,
        "Genre": tags.genre,
        "Track": _track_text(tags),
    }
    width = max(len(name) for name in fields)
    source = review.source
    lines = ["", f"Original title: {source.title}", f"Uploaded by {source.uploader}"]
    lines += [f"  {name:<{width}} {_shown(value)}" for name, value in fields.items()]
    if review.suggestion.changes:
        lines.append("Changes:")
    for number, change in enumerate(review.suggestion.changes, start=1):
        lines.append(f"  {number}. {'on ' if change.enabled else 'off'}  {change.label}")
    dest = pipeline.destination(
        source,
        review.suggestion,
        staged,
        tags,
        review.settings,
        style=review.style,
        overrides=review.overrides,
    )
    lines.append(f"Cover: {cover_text(source)}")
    lines.append(f"Audio: {audio_text(staged)}")
    lines.append(f"Saves as {dest.name} in {dest.parent}")
    return "\n".join(lines)


def _shown(value: object) -> str:
    return "(empty)" if value is None or value == "" else str(value)


def _track_text(tags: Tags) -> str | None:
    if tags.track_no is None:
        return None
    return f"{tags.track_no}/{tags.track_total}" if tags.track_total else str(tags.track_no)


def cover_text(source: SourceInfo) -> str:
    """Where the cover comes from, flagging an uploader's profile picture."""
    if source.artwork_url is None:
        return "none, since the track has no artwork"
    if source.artwork_is_avatar:
        return "the uploader's profile picture, since the track has no artwork of its own"
    return "the track's artwork"


def audio_text(staged: pipeline.Staged) -> str:
    """What was downloaded and what gets saved, like "AAC 160 kbps stream → MP3 320 kbps"."""
    got = staged.downloaded
    if got.is_original:
        source = f"Original {got.ext.upper()}"
    else:
        stream_rate = f" {got.bitrate:g} kbps" if got.bitrate else ""
        source = f"{(got.codec or got.ext).upper()}{stream_rate} stream"
    saved_rate = f" {staged.audio.bitrate} kbps" if staged.audio.bitrate else ""
    return f"{source} → {staged.fmt.upper()}{saved_rate}"


def ask_about_collision(read: Reader) -> AskFn:
    """The question finalize asks when the file's name is taken."""

    def ask(path: Path) -> CollisionChoice:
        prompt = f"{path.name} already exists in {path.parent}. Replace it, keep both, or skip?"
        while True:
            choice = COLLISION_ANSWERS.get(read(f"{prompt} [r/k/s] ").strip().lower())
            if choice is not None:
                return choice

    return ask
