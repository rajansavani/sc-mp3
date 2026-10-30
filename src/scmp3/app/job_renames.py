"""The question before a re-tag that gives a song a new title, artist or album (PLAN §4.7).

Spotify's playlists know a local song by those tags, so a save that changes them asks first.
Python decides, from the tags it would write, so a late event can't hide a change from the UI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scmp3.app.job_views import rendered_tags
from scmp3.core.models import Settings
from scmp3.core.sources import localfile

if TYPE_CHECKING:
    from scmp3.app.job_record import JobRecord

# The answer to a save that needs the question first. The UI asks, then saves with the answer.
RENAMES_SONG = "renames_song"


def asks_about_playlists(record: JobRecord, settings: Settings) -> bool:
    """Whether saving this job needs the playlist question first.

    It does when the re-tag gives the file a new title, artist or album that wasn't confirmed
    already, for example after a settings change since the question was answered.
    """
    identity = _new_identity(record, settings)
    return identity is not None and identity != record.confirmed_identity


def retry_asks_about_playlists(record: JobRecord, settings: Settings) -> bool:
    """Whether retrying this job saves a re-tag that needs the playlist question first.

    A Retry that reads the file again, as after a failed read or a change made elsewhere,
    saves nothing.
    """
    reads_again = record.failed_step == "resolve" or record.prepared is None
    if reads_again or record.error_reason == "file_changed":
        return False
    return asks_about_playlists(record, settings)


def confirm_rename(record: JobRecord, settings: Settings) -> None:
    """Remember that the question was answered for the title, artist and album saved now."""
    record.confirmed_identity = _new_identity(record, settings)


def _new_identity(record: JobRecord, settings: Settings) -> tuple[str, str, str] | None:
    """The title, artist and album a re-tag would write, when they differ from the file's."""
    job = record.job
    if job.kind != "retag" or not settings.retag_playlist_warning or job.source is None:
        return None
    tags = rendered_tags(record, settings)
    if tags is None or not localfile.changes_identity(job.source, tags):
        return None
    return (tags.title, tags.artist, tags.album)
