"""The job manager following saved settings: it resizes the download pool, and re-renders the
jobs in review on a worker, outside the lock."""

import logging

from scmp3.app.job_editing import EDITABLE_STATES
from scmp3.app.job_flow import JobFlow
from scmp3.app.job_record import JobRecord, planned_path, rebuilt_suggestion, rendered_tags
from scmp3.core.models import JobState, Settings

log = logging.getLogger(__name__)


class SettingsFollowing:
    """The job manager's call for a settings save, on its flow."""

    _flow: JobFlow

    def settings_changed(self, settings: Settings, version: int | None = None) -> None:
        """Follow saved settings: resize the download pool, and re-render the jobs in review.

        `version` numbers each save, so a change that arrives after a newer one is ignored.
        """
        flow = self._flow
        with flow.lock:
            if version is not None and version <= flow.settings_version:
                log.debug("Settings save %d came after a newer one, so it's ignored", version)
                return
            flow.settings_version = version or flow.settings_version
            before, flow.settings = flow.settings, settings
            if settings.download_while_reviewing and not before.download_while_reviewing:
                self._download_jobs_in_review()
        if before.concurrent_downloads != settings.concurrent_downloads:
            # Outside the lock, since a bigger pool starts its threads now. It reads the newest
            # setting, so two changes resizing at once still end on the latest.
            flow.resize_downloads(flow.settings.concurrent_downloads)
        # Downloads already started keep their format, and nothing downloads again.
        # Rebuilding suggestions takes a moment per job, so it isn't done on the bridge's thread.
        flow.misc_pool.submit(lambda: self._refresh_reviews(settings))

    def _refresh_reviews(self, settings: Settings) -> None:
        flow = self._flow
        with flow.lock:
            if flow.settings is not settings:
                # A newer change is on its way with its own refresh.
                return
            waiting = [
                (record, record.job.suggestion, record.job.source, dict(record.toggles))
                for record in flow.records.values()
                if record.job.state in EDITABLE_STATES
                and record.job.suggestion
                and record.job.source
            ]
        rebuilt = [
            (record, before, rebuilt_suggestion(before, source, toggles, settings))
            for record, before, source, toggles in waiting
        ]
        with flow.lock:
            if flow.settings is not settings:
                return
            for record, before, after in rebuilt:
                # A toggle made meanwhile already rebuilt it with these settings.
                if record.job.suggestion is before:
                    record.job.suggestion = after
            for record in flow.records.values():
                _drop_stale_question(record, settings)
                flow.publish(record)

    def _download_jobs_in_review(self) -> None:
        """Start the downloads that the download-while-reviewing setting would have started."""
        for record in self._flow.records.values():
            if record.job.state != JobState.needs_review or record.prepared is None:
                continue
            # A download the person stopped stays stopped until they save.
            if not record.download_cancelled:
                self._flow.start_download(record)


def _drop_stale_question(record: JobRecord, settings: Settings) -> None:
    """Drop the question about a taken name once the job would be saved under another name."""
    if record.collision is None:
        return
    tags = rendered_tags(record, settings)
    if tags is None or planned_path(record, tags, settings) != record.collision:
        record.collision = None
        record.collision_answer = None
