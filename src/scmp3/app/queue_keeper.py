"""Keeping the queue in queue.json: bringing it back at startup and writing it after each change.

job_keeping.py says what each job's entry holds. The writes happen on QueueWriter's thread, from
snapshots, so no bridge call waits for them.
"""

import logging
from collections.abc import Mapping
from typing import Any

from scmp3.app.job_flow import JobFlow
from scmp3.app.job_keeping import UNUSABLE_ENTRY, kept_entry, restored_record
from scmp3.app.job_record import JobRecord
from scmp3.app.job_steps import Steps
from scmp3.app.queue_file import QueueFile
from scmp3.app.queue_writer import QueueWriter

log = logging.getLogger(__name__)


class QueueKeeper:
    """The queue's copy in queue.json, kept up to date from the flow's changes."""

    def __init__(self, file: QueueFile, flow: JobFlow, steps: Steps) -> None:
        self._file = file
        self._flow = flow
        self._steps = steps
        self._writer = QueueWriter(file, self._entries)
        flow.on_change = self._writer.changed

    def restore(self) -> None:
        """Put the jobs from queue.json back in the queue, and start the work they wait for.

        Call it before the writer starts, since it clears temp files a crash left.
        """
        self._file.remove_leftovers()
        records, unusable = self._restored_records(self._file.load())
        if unusable:
            # The file is kept for a bug report, and the next write starts a new one.
            self._file.move_aside(f"{unusable} of its jobs couldn't be read")
        flow = self._flow
        restored: list[JobRecord] = []
        with flow.lock:
            for record in records:
                if record.id in flow.records or flow.queued_with_link(record.parsed.url):
                    log.warning("queue.json has a job twice, so only the first came back")
                    continue
                flow.restore(record)
                restored.append(record)
            snapshots = [record.snapshot() for record in restored]
            settings = flow.settings
        if not restored:
            return
        log.info("Brought back %d jobs from queue.json", len(restored))
        self._writer.changed()
        # The UI's dicts are built now, so the window's first snapshot doesn't wait for them.
        flow.misc_pool.submit(lambda: [snapshot.view(settings) for snapshot in snapshots])
        tracks = [track for record in restored for track in (record, *record.children)]
        flow.misc_pool.submit(lambda: self._recheck_duplicates(tracks))

    def _recheck_duplicates(self, records: list[JobRecord]) -> None:
        """Look each restored job up in the history again.

        A save that ended just before a crash isn't in queue.json yet, so its job came back
        unsaved, and the warning that it was saved has to come from the history.
        """
        for record in records:
            with self._flow.lock:
                source = record.job.source if record.prepared else None
            if source is None:
                continue
            found = self._steps.duplicate_of(source)
            with self._flow.lock:
                if record.closed or record.job.source is not source:
                    continue
                if found == record.job.duplicate:
                    continue
                record.job.duplicate = found
                if record.prepared is not None:
                    record.prepared.duplicate = found
                self._flow.publish(record)

    def start(self) -> None:
        """Start writing queue.json after each change."""
        self._writer.start()

    def write_now(self) -> bool:
        """Write queue.json now. Returns whether the queue will come back at the next start."""
        return self._writer.write_now()

    def close(self) -> bool:
        """Write queue.json a last time, as the app closes."""
        return self._writer.close()

    def _restored_records(self, entries: list[Mapping[str, Any]]) -> tuple[list[JobRecord], int]:
        """The jobs the entries keep, and how many entries couldn't be used."""
        settings = self._flow.settings
        records: list[JobRecord] = []
        for entry in entries:
            try:
                records.append(restored_record(entry, self._steps, settings))
            except UNUSABLE_ENTRY as error:
                log.warning("A job in queue.json couldn't be read: %s", type(error).__name__)
                log.debug("The job in queue.json that couldn't be read", exc_info=error)
        return records, len(entries) - len(records)

    def _entries(self) -> list[str]:
        """Each kept job's entry, as JSON, built outside the lock and once per change."""
        with self._flow.lock:
            snapshots = [record.snapshot() for record in self._flow.records.values()]
        entries = (snapshot.kept(kept_entry) for snapshot in snapshots)
        return [entry for entry in entries if entry is not None]
