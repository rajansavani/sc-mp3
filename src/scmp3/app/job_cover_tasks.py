"""The cover tasks: reading the source's cover once a job resolves, and checking new covers.

Both run on the misc pool. A new cover is checked before the job switches to it, and one that
can't be used leaves the job's cover as it was, with a code the UI can show.
"""

import logging
from pathlib import Path

from scmp3.app.job_book import JobBook
from scmp3.app.job_covers import (
    CoverReader,
    CoverRequest,
    CoverView,
    KeptImage,
    checked_cover,
    cover_problem,
    keep_source_cover,
)
from scmp3.app.job_record import JobRecord, Ticket
from scmp3.core import download
from scmp3.core.errors import ScmpError
from scmp3.core.models import ArtworkChoice

log = logging.getLogger(__name__)


class CoverTasks(JobBook):
    """The cover tasks, for the flow that runs every job's steps."""

    _covers: CoverReader

    def _cover_settled(self, record: JobRecord) -> None:
        """Called once a new cover is checked, so a save waiting for it can go ahead."""
        raise NotImplementedError

    def start_source_cover(self, record: JobRecord) -> None:
        """Read the source's cover for its size, and keep it for the save."""
        ticket = self._new_ticket(record, "source_cover")
        self.misc_pool.submit(lambda: self._read_source_cover(record, ticket))

    def _read_source_cover(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "source_cover", ticket):
                return
            source = record.job.source
        view: CoverView | None = None
        image: KeptImage | None = None
        try:
            if source is not None:
                view, image = self._covers.source(source)
        except ScmpError as error:
            log.warning("The source's cover couldn't be read: %s", error)
        except Exception:
            log.exception("Reading the source's cover failed unexpectedly")
        kept = self._keep_while_current(record, ticket, image)
        with self.lock:
            current = self._release(record, "source_cover", ticket)
            if current:
                if view is not None:
                    record.source_cover = view
                    record.source_cover_file = kept
                    self.publish(record)
                # A save waits for this task, so it can use the kept image.
                self._cover_settled(record)
        if not current and kept is not None:
            # The job was removed, skipped or resolved again meanwhile, so the image isn't needed.
            _delete_quietly(kept)

    def _keep_while_current(
        self, record: JobRecord, ticket: Ticket, image: KeptImage | None
    ) -> Path | None:
        """Write the source's cover into staging, unless the job has moved on meanwhile.

        This task still counts as running, so the folder isn't deleted while it writes, and a save
        waits for it to finish.
        """
        if image is None:
            return None
        with self.lock:
            if record.closed or record.tickets.get("source_cover") is not ticket:
                return None
        try:
            return keep_source_cover(image, download.new_staging(record.id))
        except ScmpError as error:
            log.warning("Couldn't keep the source's cover for the save: %s", error)
            return None

    def read_new_cover(self, record: JobRecord, request: CoverRequest) -> None:
        """Check a new cover on a worker. The job switches to it once it's known to work."""
        ticket = self._new_ticket(record, "cover")
        self.misc_pool.submit(lambda: self._read_new_cover(record, ticket, request))

    def _read_new_cover(self, record: JobRecord, ticket: Ticket, request: CoverRequest) -> None:
        with self.lock:
            if not self._claim(record, "cover", ticket):
                return
        checked: tuple[ArtworkChoice, CoverView] | None = None
        problem: str | None = None
        try:
            checked = checked_cover(self._covers, record.id, request)
        except ScmpError as error:
            log.warning("The new cover can't be used, so the job keeps its cover: %s", error)
            problem = cover_problem(error)
        except Exception:
            # Whatever failed, the job mustn't be left waiting for this cover.
            log.exception("Checking a new cover failed unexpectedly")
            problem = cover_problem(None)
        with self.lock:
            if not self._release(record, "cover", ticket):
                return
            if checked is None:
                record.cover_problem = problem
            else:
                record.job.artwork, record.added_cover = checked
                record.cover_problem = None
            self._cover_settled(record)
            self.publish(record)


def _delete_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        log.warning("Couldn't delete a cover that wasn't needed: %s", error.strerror or error)
