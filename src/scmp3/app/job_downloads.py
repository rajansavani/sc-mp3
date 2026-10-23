"""The download tasks: each job's audio, downloaded and converted into its staging folder.

Every method here expects the caller to hold `lock`, except the tasks, which take it themselves,
as job_book.py describes.
"""

import logging
from pathlib import Path

from scmp3.app.job_book import unexpected_error
from scmp3.app.job_cover_tasks import CoverTasks
from scmp3.app.job_record import JobRecord, Ticket
from scmp3.app.workers import Pool
from scmp3.core import pipeline
from scmp3.core.errors import Cancelled, ScmpError
from scmp3.core.models import Progress

# The fewest seconds between the starts of two downloads from one source, by its kind. YouTube
# asks for a bot check when downloads come too fast (PLAN §4.5). Other sources aren't paced.
DOWNLOAD_SPACING: dict[str, float] = {"youtube": 5.0}

log = logging.getLogger(__name__)


class DownloadTasks(CoverTasks):
    """The download tasks, for the flow that runs every job's steps."""

    _download_pool: Pool

    def save_if_ready(self, record: JobRecord) -> None:
        """Save a job that waited for its audio. The flow, which runs the saves, defines it."""
        raise NotImplementedError

    def start_download(self, record: JobRecord) -> None:
        if "download" in record.tickets or record.staged is not None:
            return
        ticket = self._new_ticket(record, "download")
        if record.download_running:
            # A cancelled download is still stopping, and two mustn't share the staging folder.
            record.download_deferred = True
            return
        self._submit_download(record, ticket)

    def _submit_download(self, record: JobRecord, ticket: Ticket) -> None:
        # Paced by the source's kind, as DOWNLOAD_SPACING says, unless the download was called off
        # by the time its turn comes. That's read without the lock, and a stale answer costs only
        # one turn.
        pace = record.prepared.source.kind if record.prepared else None
        self._download_pool.submit(
            lambda: self._download(record, ticket),
            pace=pace,
            wanted=lambda: not record.closed and record.tickets.get("download") is ticket,
        )

    def cancel_download(self, record: JobRecord) -> None:
        ticket = record.tickets.pop("download", None)
        if ticket is None:
            return
        ticket.cancel.set()
        record.job.progress = None

    def _download(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "download", ticket):
                return
            record.download_running = True
            prepared = record.prepared
            assert prepared is not None
            # The format is fixed now, from the settings at this moment (TECH_SPEC §9).
            prepared.settings = self.settings
            stale, record.stale_audio = record.stale_audio, None
            record.job.progress = Progress(pct=0.0)
            self.publish(record)
        outcome: pipeline.Staged | ScmpError | Cancelled
        try:
            if stale is not None:
                # Done here, so it can't delete the file this download is about to write.
                _delete_file(stale)
            outcome = self._steps.fetch(
                prepared,
                on_progress=lambda progress: self._progress(record, ticket, progress),
                cancel=ticket.cancel,
            )
        except (ScmpError, Cancelled) as error:
            outcome = error
        except Exception as error:
            outcome = unexpected_error("downloading", error)
        with self.lock:
            record.download_running = False
            current = self._release(record, "download", ticket)
            self._start_deferred_download(record)
            if not current:
                return
            record.job.progress = None
            if isinstance(outcome, ScmpError):
                self._fail(record, "download", outcome)
            elif isinstance(outcome, pipeline.Staged):
                record.staged = outcome
                record.job.staged_audio = outcome.path
                self.save_if_ready(record)
            self.publish(record)

    def _start_deferred_download(self, record: JobRecord) -> None:
        """Start the download that waited for a cancelled one to stop."""
        if not record.download_deferred:
            return
        record.download_deferred = False
        ticket = record.tickets.get("download")
        if ticket is not None:
            self._submit_download(record, ticket)

    def _progress(self, record: JobRecord, ticket: Ticket, progress: Progress) -> None:
        """Show a download's progress. It runs inside yt-dlp, so it never raises."""
        try:
            with self.lock:
                if record.closed or record.tickets.get("download") is not ticket:
                    return
                record.job.progress = progress
                # Not published, since the progress event says it, but a snapshot must show it.
                record.changed()
                self._events.emit(
                    {
                        "type": "progress",
                        "job_id": record.id,
                        "pct": progress.pct,
                        "speed": progress.speed,
                        "eta": progress.eta,
                    }
                )
        except Exception:
            # Raising here would fail the download as an internal error.
            log.exception("Showing a download's progress failed")


def _delete_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        log.warning("Couldn't delete the staged audio: %s", error.strerror or error)
