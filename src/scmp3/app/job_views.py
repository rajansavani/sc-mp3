"""The dict the UI gets for each job: the model's fields plus what TECH_SPEC §15 adds for the UI.

A set's dict holds its tracks' dicts, each tagged and named the way it would be saved in the
album, and the album's fields and cover. Snapshot.view builds them once per change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scmp3.app.job_artists import needs_main_artist
from scmp3.app.set_tracks import (
    TrackPlace,
    album_fields,
    album_is_settled,
    default_mode,
    is_numbered,
    render_track,
    saves_once_in,
    track_overrides,
    track_places,
)
from scmp3.core import pipeline
from scmp3.core.conversion_choice import choose_conversion
from scmp3.core.convert import AudioInfo
from scmp3.core.errors import ScmpError
from scmp3.core.models import FormatChoice, FormatOption, Settings, Tags
from scmp3.core.pipeline import Staged
from scmp3.core.sources import localfile
from scmp3.core.suggest import InvalidOverride
from scmp3.core.tagger import FileFormat

if TYPE_CHECKING:
    from scmp3.app.job_record import JobRecord, Snapshot

# What a stand-in for the staged file holds before the download, apart from its format.
NO_AUDIO = AudioInfo(duration=0.0, bitrate=None, codec=None, sample_rate=None)
UNKNOWN_DOWNLOAD = FormatOption(format_id="", ext="")

log = logging.getLogger(__name__)


def rendered_tags(
    record: JobRecord, settings: Settings, place: TrackPlace | None = None
) -> Tags | None:
    """The tags the job would be saved with now, or None before it's resolved.

    A track of an album set has a `place`, which gives it the album's fields and its number.
    """
    job = record.job
    if job.source is None or job.suggestion is None:
        return None
    try:
        return render_track(
            job.source,
            job.suggestion,
            settings,
            style=job.style,
            overrides=track_overrides(job, place),
            place=place,
        )
    except InvalidOverride as error:
        # update_fields checks every value first, so this is a bug.
        log.warning("A job's hand edits can't be rendered: %s", error)
        return None


def planned_format(record: JobRecord, settings: Settings) -> FileFormat:
    """The saved file's format: the staged file's, or the one the download will convert to."""
    if record.staged is not None:
        return record.staged.fmt
    choice = format_choice(record, settings)
    codec = choice.format.codec if choice else None
    return choose_conversion(settings.audio_format, codec).fmt


def format_choice(record: JobRecord, settings: Settings) -> FormatChoice | None:
    """What the download takes from the source, or None when it can't be told.

    Before the download, YouTube's choice follows the audio format setting as it is now. After
    it, the choice is what was downloaded.
    """
    prepared = record.prepared
    if prepared is None:
        return None
    if record.staged is not None:
        return FormatChoice(record.staged.downloaded)
    try:
        return pipeline.choose_format(prepared.source, settings, signed_in=bool(prepared.token))
    except ScmpError as error:
        log.info("No format to show for a job: %s", error)
        return None


def planned_path(
    record: JobRecord, tags: Tags, settings: Settings, place: TrackPlace | None = None
) -> Path | None:
    """Where the job would be saved now, before checking whether the name is taken."""
    job = record.job
    if job.source is None or job.suggestion is None:
        return None
    staged = record.staged or _before_download(record, settings)
    try:
        return pipeline.destination(
            job.source,
            job.suggestion,
            staged,
            tags,
            settings,
            style=job.style,
            overrides=track_overrides(job, place),
            in_album_set=place is not None,
        )
    except ScmpError as error:
        log.info("No file name to show for a job: %s", error)
        return None


def _before_download(record: JobRecord, settings: Settings) -> Staged:
    """A stand-in for the staged file, since destination() only reads the format from it."""
    return Staged(Path(), planned_format(record, settings), NO_AUDIO, UNKNOWN_DOWNLOAD)


def job_view(
    record: JobRecord, settings: Settings, place: TrackPlace | None = None
) -> dict[str, Any]:
    """A track as the UI gets it. A track of an album set is shown at its `place` in the album."""
    if record.job.kind == "retag":
        return retag_view(record, settings)
    view = record.job.to_dict()
    tags = rendered_tags(record, settings, place)
    path = planned_path(record, tags, settings, place) if tags else None
    choice = format_choice(record, settings)
    view["tags"] = tags.to_dict() if tags else None
    view["filename"] = _relative_name(path, settings.library_dir)
    view["format"] = choice.to_dict() if choice else None
    # A set's track still fetching its info saves once it's in, after Save on its set.
    view["saves_once_in"] = saves_once_in(record)
    # The suggestion credits the list set by hand, so this only says whether Reset applies.
    view["artists_edited"] = record.artists is not None
    view["needs_main_artist"] = needs_main_artist(record.artists, record.job.overrides)
    _add_cover_and_error(view, record)
    return view


def retag_view(record: JobRecord, settings: Settings) -> dict[str, Any]:
    """A file to re-tag as the UI gets it. It keeps its name and audio, so there's no format.

    `file_tags` holds the title, artist and album in the file now, which the UI compares with
    the new ones before it asks about playlists.
    """
    view = record.job.to_dict()
    tags = rendered_tags(record, settings)
    source = record.job.source
    view["tags"] = tags.to_dict() if tags else None
    view["filename"] = Path(record.job.input).name
    view["format"] = None
    view["saves_once_in"] = False
    view["file_tags"] = localfile.identity(source) if source else None
    _add_cover_and_error(view, record)
    return view


def set_view(snapshot: Snapshot, settings: Settings) -> dict[str, Any]:
    """A set as the UI gets it: its own fields, its tracks' dicts, and the album they share.

    `tags` holds the album fields, `default_mode` what the set's type suggests, and
    `album_settled` whether a track already saves, which fixes the album.
    """
    record = snapshot.record
    job = record.job
    tracks = [child.record for child in snapshot.children]
    places = track_places(job, tracks)
    view = job.to_dict()
    view["children"] = [
        child.view(settings, place) for child, place in zip(snapshot.children, places, strict=True)
    ]
    view["default_mode"] = default_mode(job)
    view["tags"] = _album_tags(album_fields(job, tracks)) if job.source else None
    view["filename"] = None
    view["format"] = None
    view["album_settled"] = album_is_settled(tracks)
    _add_cover_and_error(view, record)
    if view["cover_url"] is None:
        # A set with no cover of its own or its first track's shares the first resolved one's.
        view["cover_url"] = _first_track_cover(tracks)
    return view


def _add_cover_and_error(view: dict[str, Any], record: JobRecord) -> None:
    """The cover, the question about a taken name, and what the error view needs."""
    cover = record.cover
    view["cover_url"] = cover.url
    view["cover_size"] = cover.size()
    view["default_crop"] = cover.default_crop.to_dict() if cover.default_crop else None
    view["collision"] = {"path": str(record.collision)} if record.collision else None
    view["cover_missing"] = record.cover_error is not None
    view["cover_problem"] = record.cover_problem
    if view["error"] is not None:
        # The UI offers "Choose another folder" only for the library (TECH_SPEC §9).
        view["error"]["where"] = record.error_where
        view["error"]["reason"] = record.error_reason
        view["error"]["retry_at"] = record.retry_at.isoformat() if record.retry_at else None


def _album_tags(fields: dict[str, str]) -> dict[str, Any]:
    """The album fields as the set's editor shows them, typed as Tags types them."""
    year = fields.get("year", "").strip()
    return {
        "album": fields.get("album", ""),
        "album_artist": fields.get("album_artist", ""),
        "year": int(year) if year.isdigit() else None,
        "genre": fields.get("genre") or None,
    }


def _first_track_cover(tracks: list[JobRecord]) -> str | None:
    """The first included track's cover, which the album shares when the set has none."""
    first = next((track for track in tracks if is_numbered(track)), None)
    source = first.job.source if first else None
    return source.artwork_url if source else None


def _relative_name(path: Path | None, library_dir: Path) -> str | None:
    """The file's name, with its album folder when it has one."""
    if path is None:
        return None
    return path.relative_to(library_dir).as_posix()
