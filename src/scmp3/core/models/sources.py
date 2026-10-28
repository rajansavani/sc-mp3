"""What a source says about a track or a set, before any cleanup, and the formats it offers."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from scmp3.core.models.base import Model

SourceKind = Literal["soundcloud", "youtube", "file"]
Availability = Literal["ok", "preview_only", "drm", "private", "geo_blocked"]


@dataclass
class OriginalDownload(Model):
    """The artist's own upload, offered when they allow downloads."""

    ext: str | None = None  # wav, flac, mp3 and so on; only known once signed in
    needs_sign_in: bool = False  # SoundCloud offers it, but only to signed-in users


@dataclass
class FormatOption(Model):
    """One audio stream or file a source offers, as yt-dlp lists it."""

    format_id: str  # such as hls_aac_160k
    ext: str
    codec: str | None = None  # aac, mp3, opus and so on
    bitrate: float | None = None  # kbps
    is_preview: bool = False
    is_original: bool = False


@dataclass
class FormatChoice(Model):
    """The format to download, and the stream to fall back to."""

    format: FormatOption
    # A lossy original is kept only if its bitrate is at least the fallback's.
    needs_bitrate_check: bool = False
    fallback: FormatOption | None = None


@dataclass
class PlaylistEntry(Model):
    """One track in a set, as the quick listing gives it before the track is resolved."""

    url: str
    id: str | None = None
    title: str | None = None


@dataclass
class PlaylistInfo(Model):
    """A SoundCloud set: its own details and its tracks in order."""

    title: str
    owner: str
    set_type: str | None = None  # album, ep, single or compilation; None for a plain playlist
    is_album: bool = False
    release_date: date | None = None
    genre: str | None = None
    # Its own cover, or else its first track's, as SoundCloud shows a set without one.
    artwork_url: str | None = None
    entries: list[PlaylistEntry] = field(default_factory=list)  # in set order


@dataclass
class SourceInfo(Model):
    """What the source says about a track, before any cleanup."""

    kind: SourceKind
    id: str  # "soundcloud:tracks:123", "youtube:dQw4w9WgXcQ", or a file path
    url: str  # normalized
    title: str  # the raw title
    uploader: str
    artists: list[str] = field(default_factory=list)  # publisher or YouTube Music artists
    album: str | None = None
    release_date: date | None = None
    upload_date: date | None = None
    genre: str | None = None
    description: str | None = None  # can go in the comment tag instead of the link
    duration: float | None = None  # seconds
    full_duration: float | None = None  # the real length when SoundCloud only streams a preview
    artwork_url: str | None = None
    artwork_is_avatar: bool = False  # SoundCloud fell back to the uploader's avatar
    isrc: str | None = None
    label: str | None = None
    explicit: bool | None = None
    availability: Availability = "ok"
    original: OriginalDownload | None = None
    formats: list[FormatOption] = field(default_factory=list)
    playlist: PlaylistInfo | None = None
    # A file's size and modified time when it was read, so a re-tag can tell it changed since.
    file_stamp: str | None = None
