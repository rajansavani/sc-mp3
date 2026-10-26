"""The job manager's work at startup and shutdown: starting the pools, bringing back the queue,
clearing what the last run left, and stopping cleanly as the window closes."""

import logging
import time

from scmp3.app.job_flow import JobFlow
from scmp3.app.queue_keeper import QueueKeeper
from scmp3.core import download, placing
from scmp3.core.errors import ScmpError

# How long closing the window waits for a save that's placing its file, in seconds.
SHUTDOWN_SAVE_WAIT = 5.0

log = logging.getLogger(__name__)


class QueueLifecycle:
    """Starting and stopping, on the job manager's flow and queue.json keeper."""

    _flow: JobFlow
    _keeper: QueueKeeper | None

    def start_up(self) -> None:
        """Start the pools' threads, bring back the queue, and clear what the last run left behind.

        The app calls this before its window opens, so no bridge call waits for a thread to start,
        and the window's first snapshot has the queue.
        """
        for pool in self._flow.pools():
            pool.start_threads()
        if self._keeper is not None:
            self._keeper.restore()
            self._keeper.start()
        # After the restore, so the staging folders of the jobs it brought back are kept.
        self._flow.misc_pool.submit(self._clean_up_last_run)

    def keep_queue(self) -> bool:
        """Write queue.json now. Returns whether the queue will come back at the next start."""
        return self._keeper is not None and self._keeper.write_now()

    def _clean_up_last_run(self) -> None:
        with self._flow.lock:
            keep = {record.id for record in self._flow.every_record()}
            library_dir = self._flow.settings.library_dir
        try:
            download.purge_old(keep=keep)
            placing.remove_leftovers(library_dir)
        except ScmpError as error:
            log.warning("Couldn't clear what the last run left behind: %s", error)
        except OSError as error:
            # The OS's message names the folder, so only its reason goes above DEBUG.
            log.warning("Couldn't clear what the last run left behind: %s", error.strerror or error)
            log.debug("Clearing what the last run left behind failed", exc_info=error)

    def shut_down(self) -> None:
        """Stop downloads and timers as the window closes, and keep the queue in queue.json.

        The staging folders of jobs not saved yet stay, so their audio comes back with them.
        """
        flow = self._flow
        with flow.lock:
            for record in flow.every_record():
                for ticket in record.tickets.values():
                    ticket.cancel.set()
                if record.retry_timer is not None:
                    record.retry_timer.cancel()
        for pool in flow.pools():
            pool.close()
        # A save placing its file gets to finish, so its history entry is written too.
        if not flow.save_pool.wait_until_idle(SHUTDOWN_SAVE_WAIT):
            log.warning("A save was still running when sc-mp3 closed")
        # Last, so queue.json leaves out the saves that just finished.
        if self._keeper is not None:
            self._keeper.close()

    def wait_until_idle(self, timeout: float) -> bool:
        """Wait until no task is waiting or running in any pool, as tests and shutdown need."""
        deadline = time.monotonic() + timeout
        pools = self._flow.pools()
        while time.monotonic() < deadline:
            for pool in pools:
                pool.wait_until_idle(max(0.0, deadline - time.monotonic()))
            # A task can queue another in a pool already waited on, so all are checked again.
            if all(pool.wait_until_idle(0) for pool in pools):
                return True
        return False
