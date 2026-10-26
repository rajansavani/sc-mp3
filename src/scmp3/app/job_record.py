"""One queue item as the job manager tracks it, and the copy the UI's dict is built from.

The model `Job` is what the UI sees. The record adds what only Python needs: the pipeline's own
results, the tasks in flight, and the cover previews. A set's record holds a record for each of
its tracks. A `Snapshot` copies a record under the lock, so the dicts made from it can be built
outside the lock, by job_views.py.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from scmp3.app import job_views
from scmp3.app.job_covers import NO_COVER, CoverView
from scmp3.app.set_events import SentTracks
from scmp3.app.set_tracks import TrackPlace
from scmp3.app.workers import Cancellable
from scmp3.core.errors import ErrorPlace, ErrorReason, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.library import CollisionChoice
from scmp3.core.models import ArtworkChoice, Job, Settings, SourceInfo, TagSuggestion
from scmp3.core.pipeline import LoadedCover, Prepared, PreparedSet, Staged
from scmp3.core.suggest import apply_changes

# The work a job can have in flight, each run by a task on a worker. A set's listing is its
# "resolve" step.
Step = Literal["resolve", "download", "save", "source_cover", "cover"]


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
    # A set's answer for every taken name among its tracks, once the person chose one for all.
    collision_choice: CollisionChoice | None = None
    retry_timer: Cancellable | None = None
    retry_at: datetime | None = None  # when the automatic retry runs, which the error view says
    auto_retries: int = 0  # automatic retries since a step last went through, which space out
    closed: bool = False  # skipped or removed, so nothing in flight may change it
    removed: bool = False
    discarded: bool = False  # its staging folder is deleted, or about to be
    # A set's tracks, in set order, and the set a track belongs to. The tuple is replaced, never
    # changed in place, and the set's own `job.children` stays empty: snapshots copy the tracks.
    children: tuple["JobRecord", ...] = field(default=(), repr=False)
    parent: "JobRecord | None" = field(default=None, repr=False)
    prepared_set: PreparedSet | None = None  # a set's listing, once it's in
    # The album's cover, loaded by the first track saved with it, for the choice it was loaded for.
    album_cover: tuple[ArtworkChoice, LoadedCover] | None = field(default=None, repr=False)
    # A set's tracks as the UI last got them, which only the event bus's thread uses.
    sent_tracks: SentTracks = field(default_factory=SentTracks, repr=False)
    # The copy taken since the last change, which the UI's dict and queue.json are built from.
    last_snapshot: "Snapshot | None" = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.job.id

    def snapshot(self) -> "Snapshot":
        """The record as it is now, copied once per change. Call it under the lock."""
        if self.last_snapshot is None:
            children = tuple(child.snapshot() for child in self.children)
            self.last_snapshot = Snapshot(_copied(self), children)
        return self.last_snapshot

    def changed(self) -> None:
        """Note that something the UI or queue.json shows has changed, so the copy is stale.

        A track's set shows the track, so the set's copy is stale too.
        """
        self.last_snapshot = None
        if self.parent is not None:
            self.parent.changed()

    def forget_name_question(self) -> None:
        """Drop a question about a taken name, since the name it was about may have changed."""
        if self.collision is None and self.collision_answer is None:
            return
        self.collision = None
        self.collision_answer = None
        self.changed()

    @property
    def cover(self) -> CoverView:
        """The cover the UI shows for the job's current artwork choice."""
        if self.job.artwork.kind == "source":
            return self.source_cover
        if self.job.artwork.kind == "none":
            return NO_COVER
        return self.added_cover


def _copied(record: JobRecord) -> JobRecord:
    """A copy that later changes to the record don't reach.

    A shallow copy is enough, since the job manager replaces the values it changes rather than
    editing them, except for the job itself and the toggles, which are copied too.
    """
    copied = _shallow_copy(record)
    copied.job = _shallow_copy(record.job)
    copied.toggles = dict(record.toggles)
    # The tasks in flight belong to the live record only, and a set's tracks are in the snapshot.
    copied.tickets = {}
    copied.children = ()
    copied.parent = None
    copied.last_snapshot = None
    return copied


def _shallow_copy[T](value: T) -> T:
    """copy.copy for a plain dataclass, in a few times less time, since a big paste makes 2,000."""
    copied = object.__new__(type(value))
    copied.__dict__.update(value.__dict__)
    return copied


@dataclass(eq=False)
class Snapshot:
    """A record as it was at one change. Each dict made from it is built once, outside the lock.

    A set's snapshot holds its tracks' snapshots, so a track that didn't change keeps its dict.
    Two threads building the same dict at once is harmless, since both build the same thing.
    """

    record: JobRecord
    children: tuple["Snapshot", ...] = ()
    _view: tuple[Settings, TrackPlace | None, dict[str, Any]] | None = None
    _kept: tuple[str | None] | None = None

    def view(self, settings: Settings, place: TrackPlace | None = None) -> dict[str, Any]:
        """The job as the UI gets it with these settings, a set's track at its `place` in the
        album. Callers mustn't change the dict."""
        cached = self._view
        if cached is not None and cached[0] is settings and cached[1] == place:
            return cached[2]
        if self.record.job.kind == "set":
            view = job_views.set_view(self, settings)
        else:
            view = job_views.job_view(self.record, settings, place)
        self._view = (settings, place, view)
        return view

    def kept(self, build: Callable[["Snapshot"], str | None]) -> str | None:
        """What queue.json keeps of the job, as `build` makes it from the snapshot, built once."""
        if self._kept is None:
            self._kept = (build(self),)
        return self._kept[0]


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
