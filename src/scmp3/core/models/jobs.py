"""Queue items and what each one carries: its state, cover choice, progress, error and history."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from scmp3.core.errors import ErrorKind
from scmp3.core.models.base import Model
from scmp3.core.models.settings import TitleStyle
from scmp3.core.models.sources import SourceInfo
from scmp3.core.models.tags import TagSuggestion

JobKind = Literal["track", "set", "retag"]
# "source" is the cover the source itself offers, or the one already in a file being re-tagged.
ArtworkKind = Literal["source", "url", "file", "none"]
AlbumMode = Literal["album", "singles"]


class JobState(StrEnum):
    """Where a job is: queued → resolving → needs_review → saving → saved, or skipped or error.

    The background download isn't a state. `Job.progress` tracks it while it runs, and
    `Job.staged_audio` is set when it's done.
    """

    queued = "queued"
    resolving = "resolving"
    needs_review = "needs_review"
    saving = "saving"
    saved = "saved"
    skipped = "skipped"
    error = "error"


@dataclass
class CropBox(Model):
    """A crop in fractions of the source image's width and height, so it survives resizing."""

    left: float
    top: float
    width: float
    height: float


@dataclass
class ArtworkChoice(Model):
    """Where the cover comes from and how to crop it."""

    kind: ArtworkKind = "source"
    value: str | None = None  # the URL or the file path; unused for "source" and "none"
    crop: CropBox | None = None  # None means a centered square


@dataclass
class Progress(Model):
    """How far a download has got."""

    pct: float | None = None  # 0 to 100; None while the size is unknown
    speed: float | None = None  # bytes per second
    eta: float | None = None  # seconds left
    downloaded: int | None = None  # bytes
    total: int | None = None  # bytes


@dataclass
class JobError(Model):
    """Why a job stopped."""

    kind: ErrorKind
    detail: str | None = None


@dataclass
class HistoryEntry(Model):
    """One saved file, as the history keeps it."""

    source_id: str | None
    url_norm: str | None
    saved_path: Path
    title: str
    artist: str
    album: str
    format: str  # the saved file's format, such as mp3
    bitrate: int | None = None  # kbps
    saved_at: datetime | None = None  # filled in when it's added
    id: int | None = None  # filled in by the database


@dataclass
class Job(Model):
    """One queue item: a track, a set or a file to re-tag."""

    id: str
    kind: JobKind
    input: str  # the link or file path as it was given
    state: JobState = JobState.queued
    source: SourceInfo | None = None  # None until resolved
    suggestion: TagSuggestion | None = None  # None until resolved
    overrides: dict[str, str] = field(default_factory=dict)  # fields the user typed by hand
    style: TitleStyle | None = None  # this track's title style, when it differs from the setting
    artwork: ArtworkChoice = field(default_factory=ArtworkChoice)
    staged_audio: Path | None = None
    output_path: Path | None = None
    error: JobError | None = None
    progress: Progress | None = None
    duplicate: HistoryEntry | None = None  # an earlier save of the same source
    children: list["Job"] = field(default_factory=list)  # a set's tracks
    # None lets the set type decide between saving as an album and as singles.
    album_mode: AlbumMode | None = None
    included: bool = True  # whether this track of a set gets saved
