"""The set tasks: listing a set, adding a record for each of its tracks, and what an album's saves
wait for.

Each track then resolves, downloads and saves through the same steps as a track link, one at a
time, with its own progress, cancel and name question. Every method here expects the caller to
hold `lock`, except the tasks, which take it themselves, as job_book.py describes.
"""

import logging
import uuid
from collections.abc import Callable

from scmp3.app.job_book import unexpected_error
from scmp3.app.job_covers import CoverView
from scmp3.app.job_downloads import DownloadTasks
from scmp3.app.job_record import JobRecord, Ticket
from scmp3.app.job_saving import CollisionQuestion, cover_to_save
from scmp3.app.job_set_saving import AlbumCover, save_set_track, set_track_order
from scmp3.app.set_tracks import TrackPlace, album_mode, set_source, track_places, waits_for_info
from scmp3.app.workers import Pool
from scmp3.core import pipeline
from scmp3.core.errors import ScmpError
from scmp3.core.inputs import classify
from scmp3.core.models import Job, JobState, PlaylistEntry
from scmp3.core.pipeline import PreparedSet

# The steps of a set's own record that an album's saves wait for, so they share its cover.
ALBUM_COVER_STEPS = ("cover", "source_cover")

SaveRun = Callable[[], pipeline.Saved | CollisionQuestion | None]

log = logging.getLogger(__name__)


class SetTasks(DownloadTasks):
    """The set tasks, for the flow that runs every job's steps."""

    _resolve_pool: Pool

    def _start_resolving(self, record: JobRecord) -> None:
        """Resolve a track, or list a set. The flow, which resolves tracks, defines it."""
        raise NotImplementedError

    # Listing

    def _start_listing(self, record: JobRecord) -> None:
        record.job.state = JobState.queued
        ticket = self._new_ticket(record, "resolve")
        self._resolve_pool.submit(
            lambda: self._list(record, ticket),
            pace=pipeline.LINK_SOURCES.get(record.parsed.kind),
            wanted=lambda: not record.closed and record.tickets.get("resolve") is ticket,
        )

    def _list(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "resolve", ticket):
                return
            record.job.state = JobState.resolving
            self.publish(record)
            settings = self.settings
        outcome: PreparedSet | ScmpError
        try:
            outcome = self._steps.prepare_set(record.parsed, settings=settings)
        except ScmpError as error:
            outcome = error
        except Exception as error:
            outcome = unexpected_error("listing the set", error)
        with self.lock:
            if not self._release(record, "resolve", ticket):
                return
            if isinstance(outcome, ScmpError):
                self._fail(record, "resolve", outcome)
                self.publish(record)
            else:
                self._listed(record, outcome)

    def _listed(self, record: JobRecord, prepared_set: PreparedSet) -> None:
        """Add a record for each track of the set, and start resolving them in set order."""
        self._step_worked(record)
        job = record.job
        record.prepared_set = prepared_set
        job.source = set_source(prepared_set)
        job.error = None
        self.links.add(prepared_set.url, record)
        tracks = tuple(_track_of(record, entry) for entry in prepared_set.playlist.entries)
        record.children = tracks
        self.add_tracks(tracks)
        for track in tracks:
            self._start_resolving(track)
        record.source_cover = CoverView(url=prepared_set.playlist.artwork_url)
        if prepared_set.playlist.artwork_url:
            self.start_source_cover(record)
        self.publish(record)

    # Places in the album

    def place_of(self, record: JobRecord) -> TrackPlace | None:
        """A set's track's place in its album as things are now, or None for a single."""
        parent = record.parent
        if parent is None:
            return None
        places = track_places(parent.job, parent.children)
        index = next(at for at, track in enumerate(parent.children) if track is record)
        return places[index]

    def album_waits(self, record: JobRecord) -> bool:
        """Whether a set's album saves wait: for its cover, or for a track's info, which would
        change the numbers. Tracks saved as singles never wait."""
        if album_mode(record.job) != "album":
            return False
        if any(step in record.tickets for step in ALBUM_COVER_STEPS):
            return True
        return any(waits_for_info(track) for track in record.children)

    # Saving

    def _set_track_save(self, record: JobRecord) -> SaveRun:
        """The save of a set's track, taken now and run on the save worker."""
        order = set_track_order(record, self.settings, self.place_of(record))
        parent = record.parent
        assert parent is not None

        def run() -> pipeline.Saved | CollisionQuestion | None:
            saved = save_set_track(order, self._steps)
            if saved.album_cover is not None:
                with self.lock:
                    self._keep_album_cover(parent, saved.album_cover)
            return saved.outcome

        return run

    def _keep_album_cover(self, record: JobRecord, cover: AlbumCover) -> None:
        """Keep the album's cover for its other tracks, unless the set's cover changed meanwhile."""
        if not record.closed and cover_to_save(record) == cover[0]:
            record.album_cover = cover


def _track_of(parent: JobRecord, entry: PlaylistEntry) -> JobRecord:
    """A record for one track of a set, which resolves the way a track link does."""
    job = Job(id=uuid.uuid4().hex, kind="track", input=entry.url)
    return JobRecord(job=job, parsed=classify(entry.url), parent=parent)
