"""One queue item as the job manager tracks it, and the dict the UI gets for it.

The model `Job` is what the UI sees. The record adds what only Python needs: the pipeline's own
results, the tasks in flight, and the cover previews.
"""

import logging
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from scmp3.app.job_covers import NO_COVER, CoverView
from scmp3.app.workers import Cancellable
from scmp3.core import pipeline
from scmp3.core.conversion_choice import choose_conversion
from scmp3.core.convert import AudioInfo
from scmp3.core.errors import ErrorPlace, ErrorReason, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.library import CollisionChoice
from scmp3.core.models import (
    FormatChoice,
    FormatOption,
    Job,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
)
from scmp3.core.pipeline import Prepared, Staged
from scmp3.core.sources.soundcloud_formats import choose_format
from scmp3.core.suggest import InvalidOverride, apply_changes
from scmp3.core.tagger import FileFormat

# The work a job can have in flight, each run by a task on a worker.
Step = Literal["resolve", "download", "save", "source_cover", "cover"]

# What a stand-in for the staged file holds before the download, apart from its format.
NO_AUDIO = AudioInfo(duration=0.0, bitrate=None, codec=None, sample_rate=None)
UNKNOWN_DOWNLOAD = FormatOption(format_id="", ext="")

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class Ticket:
    """One task's claim on a step. A task whose ticket is no longer the step's drops its result."""

    # Set to stop a running download.
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass
class JobRecord:
    """A job, the pipeline's results for it, and the work in flight."""

    job: Job
    parsed: ParsedInput  # what add_inputs got, so a retry can resolve it again
    prepared: Prepared | None = None
    staged: Staged | None = None
    tickets: dict[Step, Ticket] = field(default_factory=dict)
    busy: int = 0  # tasks running for this job right now
    saving_now: bool = False  # finalize is running, so it can't be called back
    remove_when_saved: bool = False  # removed while finalize ran, so it leaves once that's done
    download_running: bool = False  # a download task is running, maybe one already cancelled
    download_deferred: bool = False  # a new download waits for a cancelled one to stop
    # Audio that failed to convert, deleted by the next download before it writes its own.
    stale_audio: Path | None = None
    # The person stopped this job's download, so turning the setting on doesn't restart it.
    download_cancelled: bool = False
    wants_save: bool = False  # Save was pressed, or the job saves without review
    failed_step: Step | None = None
    error_where: ErrorPlace | None = None  # which folder a disk error was about
    error_reason: ErrorReason | None = None  # what went wrong more exactly, as with a file in use
    cover_error: ScmpError | None = None  # why a saved file has no cover
    toggles: dict[str, bool] = field(default_factory=dict)  # cleanups switched by hand
    source_cover: CoverView = NO_COVER
    source_cover_file: Path | None = None  # the source's cover, kept so a save reuses it
    added_cover: CoverView = NO_COVER  # a link, file or pasted image chosen instead
    cover_problem: str | None = None  # why the last new cover couldn't be used, as a code
    collision: Path | None = None  # the existing file the user is asked about
    # The user's answer, with the file it was about, so it never applies to another file.
    collision_answer: tuple[Path, CollisionChoice] | None = None
    retry_timer: Cancellable | None = None
    closed: bool = False  # skipped or removed, so nothing in flight may change it
    removed: bool = False
    discarded: bool = False  # its staging folder is deleted, or about to be

    @property
    def id(self) -> str:
        return self.job.id

    @property
    def cover(self) -> CoverView:
        """The cover the UI shows for the job's current artwork choice."""
        if self.job.artwork.kind == "source":
            return self.source_cover
        if self.job.artwork.kind == "none":
            return NO_COVER
        return self.added_cover


def rebuilt_suggestion(
    suggestion: TagSuggestion, source: SourceInfo, toggles: dict[str, bool], settings: Settings
) -> TagSuggestion:
    """The suggestion rebuilt with these settings. Cleanups switched by hand stay as they were."""
    by_hand = [
        replace(change, enabled=toggles[change.id])
        for change in suggestion.changes
        if change.id in toggles
    ]
    return apply_changes(replace(suggestion, changes=by_hand), source, settings)


def rendered_tags(record: JobRecord, settings: Settings) -> Tags | None:
    """The tags the job would be saved with now, or None before it's resolved."""
    job = record.job
    if job.source is None or job.suggestion is None:
        return None
    try:
        return pipeline.render_tags(
            job.source, job.suggestion, settings, style=job.style, overrides=job.overrides
        )
    except InvalidOverride as error:
        # update_fields checks every value first, so this is a bug.
        log.warning("A job's hand edits can't be rendered: %s", error)
        return None


def planned_format(record: JobRecord, settings: Settings) -> FileFormat:
    """The saved file's format: the staged file's, or the one the download will convert to."""
    if record.staged is not None:
        return record.staged.fmt
    choice = format_choice(record)
    codec = choice.format.codec if choice else None
    return choose_conversion(settings.audio_format, codec).fmt


def format_choice(record: JobRecord) -> FormatChoice | None:
    """What the download takes from the source, or None when it can't be told."""
    prepared = record.prepared
    if prepared is None:
        return None
    try:
        return choose_format(prepared.source, signed_in=bool(prepared.token))
    except ScmpError as error:
        log.info("No format to show for a job: %s", error)
        return None


def planned_path(record: JobRecord, tags: Tags, settings: Settings) -> Path | None:
    """Where the job would be saved now, before checking whether the name is taken."""
    job = record.job
    if job.source is None or job.suggestion is None:
        return None
    staged = record.staged or _before_download(record, settings)
    try:
        return pipeline.destination(
            job.source,
            job.suggestion,
            staged,
            tags,
            settings,
            style=job.style,
            overrides=job.overrides,
        )
    except ScmpError as error:
        log.info("No file name to show for a job: %s", error)
        return None


def _before_download(record: JobRecord, settings: Settings) -> Staged:
    """A stand-in for the staged file, since destination() only reads the format from it."""
    return Staged(Path(), planned_format(record, settings), NO_AUDIO, UNKNOWN_DOWNLOAD)


def job_view(record: JobRecord, settings: Settings) -> dict[str, Any]:
    """The job as the UI gets it: the model's fields plus what TECH_SPEC §15 adds for the UI."""
    view = record.job.to_dict()
    tags = rendered_tags(record, settings)
    path = planned_path(record, tags, settings) if tags else None
    choice = format_choice(record)
    cover = record.cover
    view["tags"] = tags.to_dict() if tags else None
    view["filename"] = _relative_name(path, settings.library_dir)
    view["format"] = choice.to_dict() if choice else None
    view["cover_url"] = cover.url
    view["cover_size"] = cover.size()
    view["collision"] = {"path": str(record.collision)} if record.collision else None
    view["cover_missing"] = record.cover_error is not None
    view["cover_problem"] = record.cover_problem
    if view["error"] is not None:
        # The UI offers "Choose another folder" only for the library (TECH_SPEC §9).
        view["error"]["where"] = record.error_where
        view["error"]["reason"] = record.error_reason
    return view


def _relative_name(path: Path | None, library_dir: Path) -> str | None:
    """The file's name, with its album folder when it has one."""
    if path is None:
        return None
    return path.relative_to(library_dir).as_posix()
