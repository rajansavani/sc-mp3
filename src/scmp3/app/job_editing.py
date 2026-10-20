"""The job manager's editing calls: hand edits, cleanups, title styles and covers.

Edits apply to jobs in review, and to jobs whose save failed, so they can be fixed and saved again.
"""

import logging
from dataclasses import replace
from typing import Any

from scmp3.app.job_covers import CoverRequest
from scmp3.app.job_fields import field_problems, render_problem
from scmp3.app.job_flow import JobFlow
from scmp3.app.job_record import JobRecord, rebuilt_suggestion
from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind
from scmp3.core.models import (
    ArtworkChoice,
    CropBox,
    JobState,
    TitleStyle,
)
from scmp3.core.suggest import InvalidOverride

# What calls answer when they can't act. web/strings/shell.js has the text for each.
UNKNOWN_JOB = "unknown_job"
NOT_EDITABLE = "not_editable"
INVALID_FIELDS = "invalid_fields"
INTERNAL = ErrorKind.internal.value

# States whose tags can still change. A failed save can be fixed and saved again.
EDITABLE_STATES = frozenset({JobState.needs_review, JobState.error})

Answer = dict[str, Any]

log = logging.getLogger(__name__)


def _unchanged_image(
    current: ArtworkChoice, kind: str, value: str | None, crop: CropBox | None
) -> ArtworkChoice | None:
    """The choice when it needs no new image: the source's own, or the same one recropped."""
    if kind == "source":
        return ArtworkChoice("source", None, crop)
    if kind == current.kind and value == current.value:
        return replace(current, crop=crop)
    return None


class JobEditing:
    """The editing calls, on the job manager's flow."""

    _flow: JobFlow

    def update_fields(self, job_id: str, patch: dict[str, str]) -> Answer:
        """Keep hand edits exactly as typed. A patch with any unusable value changes nothing."""
        problems = field_problems(patch)
        if problems:
            return {"error": INVALID_FIELDS, "fields": problems}
        with self._flow.lock:
            record, refusal = self._editable(job_id)
            if record is None:
                return refusal
            job = record.job
            overrides = {**job.overrides, **patch}
            try:
                # Rendering is the last word, as it's what a save does with the values.
                pipeline.render_tags(
                    job.source,
                    job.suggestion,
                    self._flow.settings,
                    style=job.style,
                    overrides=overrides,
                )
            except InvalidOverride as error:
                return {"error": INVALID_FIELDS, "fields": render_problem(error)}
            job.overrides = overrides
            self._edited(record)
        return {}

    def reset_field(self, job_id: str, field: str) -> Answer:
        """Drop a hand edit, so the field follows the suggestion again."""
        with self._flow.lock:
            record, refusal = self._editable(job_id)
            if record is None:
                return refusal
            if field in record.job.overrides:
                record.job.overrides = {
                    key: value for key, value in record.job.overrides.items() if key != field
                }
                self._edited(record)
        return {}

    def toggle_change(self, job_id: str, change_id: str, enabled: bool) -> Answer:
        """Turn one cleanup on or off, which re-renders every field not typed by hand."""
        with self._flow.lock:
            record, refusal = self._editable(job_id)
            if record is None:
                return refusal
            job = record.job
            if change_id not in {change.id for change in job.suggestion.changes}:
                log.warning("The UI toggled a cleanup the job doesn't have")
                return {"error": INTERNAL}
            record.toggles[change_id] = enabled
            job.suggestion = rebuilt_suggestion(
                job.suggestion, job.source, record.toggles, self._flow.settings
            )
            self._edited(record)
        return {}

    def set_style(self, job_id: str, style: TitleStyle) -> Answer:
        """Use a title style for this track other than the setting's."""
        with self._flow.lock:
            record, refusal = self._editable(job_id)
            if record is None:
                return refusal
            record.job.style = style
            self._edited(record)
        return {}

    def set_artwork(
        self, job_id: str, kind: str, value: str | None, crop: CropBox | None
    ) -> Answer:
        """Change the cover. The same image again only changes the crop.

        A new link, file or pasted image is checked on a worker first, and the job keeps its
        cover until then.
        """
        with self._flow.lock:
            record, refusal = self._editable(job_id)
            if record is None:
                return refusal
            # A problem with an earlier cover no longer applies.
            record.cover_problem = None
            choice = _unchanged_image(record.job.artwork, kind, value, crop)
            if choice is None:
                assert value is not None
                self._flow.read_new_cover(record, CoverRequest(kind, value, crop))
                return {}
            # A cover still being checked would replace this choice, so it's called off.
            record.tickets.pop("cover", None)
            record.job.artwork = choice
            self._edited(record)
        return {}

    def _editable(self, job_id: str) -> tuple[JobRecord, None] | tuple[None, Answer]:
        """The job to edit, or the answer that says why it can't be edited."""
        record = self._flow.records.get(job_id)
        if record is None:
            return None, {"error": UNKNOWN_JOB}
        job = record.job
        if job.state not in EDITABLE_STATES or job.suggestion is None or record.saving_now:
            return None, {"error": NOT_EDITABLE}
        return record, None

    def _edited(self, record: JobRecord) -> None:
        # An edit can change the file name, so a question about the old name no longer applies.
        record.collision = None
        record.collision_answer = None
        self._flow.publish(record)
