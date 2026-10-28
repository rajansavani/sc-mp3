"""Bringing jobs back after a restart, and running a failed job's step again.

Every method here expects the caller to hold `lock`, as job_book.py describes.
"""

from scmp3.app.job_covers import NO_COVER
from scmp3.app.job_record import JobRecord
from scmp3.app.job_set_tasks import SetTasks
from scmp3.core.errors import ErrorKind
from scmp3.core.models import ArtworkChoice, JobState


class RetryTasks(SetTasks):
    """Restoring and retrying jobs, for the flow that runs every job's steps."""

    def start_saving(self, record: JobRecord) -> None:
        """Save a job once its audio is in. The flow, which runs the saves, defines it."""
        raise NotImplementedError

    # Bringing jobs back after a restart

    def restore(self, record: JobRecord) -> None:
        """Put a job from queue.json at the end of the queue, and start the work it waits for,
        and its tracks' if it's a set."""
        self.add_record(record)
        for job_record in (record, *record.children):
            self._resume(job_record)

    def _resume(self, record: JobRecord) -> None:
        job = record.job
        if job.state == JobState.queued:
            self._start_resolving(record)
        elif job.state == JobState.needs_review and record.prepared is not None:
            # A download the person stopped stays stopped, as it was before the restart.
            wanted = self.settings.download_while_reviewing and job.included
            if wanted and not record.download_cancelled:
                self.start_download(record)
        elif job.error is not None:
            self._schedule_auto_retry(record, job.error)

    # Retrying

    def retry(self, record: JobRecord) -> None:
        """Run the failed step again, then carry on to where the job was going (TECH_SPEC §15)."""
        job = record.job
        kind = job.error.kind if job.error else None
        self._cancel_auto_retry(record)
        job.error = None
        if kind == ErrorKind.convert_failed and record.staged is not None:
            # The staged audio may be what failed, so it's downloaded again.
            record.stale_audio = record.staged.path
            record.staged = None
            job.staged_audio = None
        if record.error_reason == "file_changed":
            _forget_edits(record)
        if record.failed_step == "resolve" or record.prepared is None:
            self._start_resolving(record)
        elif record.failed_step == "download" and not record.wants_save:
            job.state = JobState.needs_review
            self.start_download(record)
        else:
            self.start_saving(record)
        self.publish(record)


def _forget_edits(record: JobRecord) -> None:
    """Drop a re-tag's edits so the file is read again, after another program changed it.

    The edits were made against what the file held before, so the review starts over from what
    it holds now.
    """
    job = record.job
    job.overrides = {}
    job.style = None
    job.artwork = ArtworkChoice()
    record.toggles = {}
    record.artists = None
    record.added_cover = NO_COVER
    record.prepared = None
    record.failed_step = "resolve"
