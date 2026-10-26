"""What queue.json keeps of each job, and how the job comes back from it after a restart.

Saved and skipped jobs aren't kept. Work in flight starts over: a job fetching its info fetches it
again, and a job that was saving goes back to review, so nothing saves after a restart unseen.
Staged audio comes back while its file is still in the job's staging folder. A set keeps each of
its tracks in its own entry, the saved ones too, so the album keeps its numbers.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, get_args

from scmp3.app.job_covers import CoverView
from scmp3.app.job_record import JobRecord, Snapshot, Step
from scmp3.app.job_steps import Steps
from scmp3.app.set_tracks import set_state
from scmp3.core import paths
from scmp3.core.convert import AudioInfo
from scmp3.core.download import JOB_ID_PATTERN
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import FormatOption, Job, JobState, Settings
from scmp3.core.pipeline import Staged
from scmp3.core.tagger import FileFormat

# The state each kept job comes back in.
RESTORED_STATES: dict[JobState, JobState] = {
    JobState.queued: JobState.queued,
    JobState.resolving: JobState.queued,
    JobState.needs_review: JobState.needs_review,
    JobState.saving: JobState.needs_review,
    JobState.error: JobState.error,
}
# A set's tracks come back saved or skipped too, so the album knows which are done.
TRACK_STATES: dict[JobState, JobState] = {
    **RESTORED_STATES,
    JobState.saved: JobState.saved,
    JobState.skipped: JobState.skipped,
}
STEPS: tuple[str, ...] = get_args(Step)
FILE_FORMATS: tuple[str, ...] = get_args(FileFormat)

# What a queue.json entry that can't be used raises while it's read.
UNUSABLE_ENTRY = (KeyError, TypeError, ValueError, AttributeError)

log = logging.getLogger(__name__)


def kept_entry(snapshot: Snapshot) -> str | None:
    """The job's entry in queue.json, as JSON, or None for a job that isn't kept."""
    record = snapshot.record
    if record.closed or record.job.state not in RESTORED_STATES:
        return None
    entry = _entry(record, RESTORED_STATES)
    if record.job.kind == "set":
        entry["children"] = [_entry(child.record, TRACK_STATES) for child in snapshot.children]
    return json.dumps(entry, ensure_ascii=False)


def _entry(record: JobRecord, states: Mapping[JobState, JobState]) -> dict[str, Any]:
    """What queue.json keeps of one job or track, in the state it comes back in."""
    # Kept as it comes back, so a change only to the progress or to a state that comes back the
    # same, such as queued to resolving, leaves the entry as it was and writes nothing.
    job_dict = record.job.to_dict()
    job_dict["state"] = states[record.job.state].value
    job_dict["progress"] = None
    return {
        "job": job_dict,
        "parsed": _parsed_dict(record.parsed),
        "staged": _staged_dict(record.staged),
        "toggles": record.toggles,
        "failed_step": record.failed_step,
        "error_where": record.error_where,
        "error_reason": record.error_reason,
        "download_cancelled": record.download_cancelled,
        "source_cover": asdict(record.source_cover),
        "source_cover_file": _path_text(record.source_cover_file),
        "added_cover": asdict(record.added_cover),
        "cover_problem": record.cover_problem,
        "stale_audio": _path_text(record.stale_audio),
    }


def restored_record(
    entry: Mapping[str, Any],
    steps: Steps,
    settings: Settings,
    *,
    parent: JobRecord | None = None,
) -> JobRecord:
    """The job a queue.json entry kept, ready to go back in the queue, or a track of `parent`.

    Raises one of UNUSABLE_ENTRY for an entry that can't be used.
    """
    job = Job.from_dict(entry["job"])
    if not JOB_ID_PATTERN.fullmatch(job.id):
        raise ValueError("the job id can't be a staging folder's name")
    if job.kind == "set" and parent is not None:
        raise ValueError("a set's track can't be a set")
    record = JobRecord(job=job, parsed=_parsed(entry["parsed"]), parent=parent)
    record.toggles = {str(key): value is True for key, value in entry.get("toggles", {}).items()}
    record.error_where = entry.get("error_where")
    record.error_reason = entry.get("error_reason")
    record.download_cancelled = entry.get("download_cancelled") is True
    record.source_cover = CoverView(**entry.get("source_cover", {}))
    record.added_cover = CoverView(**entry.get("added_cover", {}))
    record.cover_problem = entry.get("cover_problem")
    record.source_cover_file = _kept_file(entry.get("source_cover_file"), job.id)
    record.stale_audio = _kept_file(entry.get("stale_audio"), job.id)
    failed_step = entry.get("failed_step")
    record.failed_step = failed_step if failed_step in STEPS else None
    if job.kind == "set":
        _restore_set(record, entry, steps, settings)
    else:
        states = RESTORED_STATES if parent is None else TRACK_STATES
        _restore_state(record, entry, steps, settings, states)
    return record


def _restore_set(
    record: JobRecord, entry: Mapping[str, Any], steps: Steps, settings: Settings
) -> None:
    """Put a set back with its tracks, or to be listed again if it wasn't listed yet."""
    job = record.job
    job.state = RESTORED_STATES[job.state]
    job.progress = None
    job.children = []
    playlist = job.source.playlist if job.source else None
    if playlist is None or job.state == JobState.queued:
        job.state = JobState.error if job.error is not None else JobState.queued
        record.failed_step = "resolve"
        return
    assert job.source is not None
    record.prepared_set = steps.restore_set(job.source.url, playlist, settings=settings)
    children = entry["children"]
    if len(children) != len(playlist.entries):
        raise ValueError("a set came back with more or fewer tracks than its listing")
    record.children = tuple(
        restored_record(child, steps, settings, parent=record) for child in children
    )
    job.state = set_state(record.children)


def _restore_state(
    record: JobRecord,
    entry: Mapping[str, Any],
    steps: Steps,
    settings: Settings,
    states: Mapping[JobState, JobState],
) -> None:
    """Put the job in the state it comes back in, with what the pipeline had for it."""
    job = record.job
    job.state = states[job.state]
    job.progress = None
    if job.state == JobState.error and job.error is None:
        # Nothing says what went wrong, so the job starts over.
        job.state = JobState.queued
    source, suggestion = job.source, job.suggestion
    if source is not None and suggestion is not None and job.state != JobState.queued:
        record.prepared = steps.restore(
            job.id, source, suggestion, job.duplicate, settings=settings
        )
        record.staged = _staged(entry.get("staged"), job.id)
    elif job.state == JobState.error:
        # It failed before it had its info, so Retry fetches it.
        record.failed_step = "resolve"
    else:
        job.state = JobState.queued
    job.staged_audio = record.staged.path if record.staged else None


def _parsed_dict(parsed: ParsedInput) -> dict[str, Any]:
    return {
        "kind": parsed.kind,
        "raw": parsed.raw,
        "url": parsed.url,
        "path": _path_text(parsed.path),
    }


def _parsed(data: Mapping[str, Any]) -> ParsedInput:
    path = data.get("path")
    return ParsedInput(
        kind=data["kind"],
        raw=str(data["raw"]),
        url=data.get("url"),
        path=Path(path) if path else None,
    )


def _staged_dict(staged: Staged | None) -> dict[str, Any] | None:
    if staged is None:
        return None
    return {
        "path": str(staged.path),
        "fmt": staged.fmt,
        "audio": asdict(staged.audio),
        "downloaded": staged.downloaded.to_dict(),
    }


def _staged(data: Mapping[str, Any] | None, job_id: str) -> Staged | None:
    """The staged audio again, or None when its file has gone and it has to be downloaded."""
    if not data:
        return None
    path = _kept_file(data["path"], job_id)
    if path is None:
        log.info("A job's staged audio has gone, so it's downloaded again when it's needed")
        return None
    fmt = data["fmt"]
    if fmt not in FILE_FORMATS:
        raise ValueError(f"the staged audio has an unknown format: {fmt!r}")
    return Staged(
        path=path,
        fmt=fmt,
        audio=AudioInfo(**data["audio"]),
        downloaded=FormatOption.from_dict(data["downloaded"]),
    )


def _kept_file(text: str | None, job_id: str) -> Path | None:
    """A file kept in the job's staging folder, or None when it's missing or kept elsewhere.

    Only that folder counts, since a save moves the staged file into the library.
    """
    if not text:
        return None
    path = Path(text)
    if path.parent != paths.staging_dir() / job_id or not path.is_file():
        return None
    return path


def _path_text(path: Path | None) -> str | None:
    return str(path) if path is not None else None
