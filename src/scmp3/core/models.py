"""The data passed between core, the app and the UI, and the settings.

Every model converts to a JSON-safe dict and back, for the bridge and queue.json. Dates become
ISO strings, paths become strings and enums become their values.
"""

import functools
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum, StrEnum
from pathlib import Path, PurePath
from types import NoneType, UnionType
from typing import Any, Literal, Self, Union, get_args, get_origin, get_type_hints

from scmp3.core.errors import ErrorKind
from scmp3.core.paths import default_library_dir

log = logging.getLogger(__name__)

SourceKind = Literal["soundcloud", "youtube", "file"]
Availability = Literal["ok", "preview_only", "drm", "private", "geo_blocked"]
ChangeKind = Literal[
    "remove_promo",
    "remove_producer",
    "extract_feature",
    "artist_from_title",
    "strip_redundant_artist",
    "remove_emoji",
    "fix_caps",
    "custom_word",
]
JobKind = Literal["track", "set", "retag"]
# "source" is the cover the source itself offers, or the one already in a file being re-tagged.
ArtworkKind = Literal["source", "url", "file", "none"]
AlbumMode = Literal["album", "singles"]
AccountTier = Literal["free", "go", "go_plus", "dj"]

# The choices offered in the options menu.
TitleStyle = Literal["artist_field", "feat_in_title", "all_in_title", "custom"]
CollisionPolicy = Literal["ask", "keep_both", "replace", "skip"]
ArtistSeparator = Literal[", ", " & ", " x "]
FeatWording = Literal["feat.", "ft.", "featuring", "with"]
SinglesAlbum = Literal["title", "fixed", "blank"]
AudioFormat = Literal["mp3_320", "mp3_v0", "m4a"]
CoverSize = Literal[300, 640, 1000, 1400]
CommentMode = Literal["source_url", "description", "none"]
DownloadsAtOnce = Literal[1, 2, 3, 4]
EngineChannel = Literal["stable", "nightly"]

# SoundCloud's consumer_subscription.product.id for each plan. Free accounts send either id.
PRODUCT_TIERS: dict[str, AccountTier] = {
    "free": "free",
    "consumer-free": "free",
    "consumer-mid-tier": "go",
    "consumer-high-tier": "go_plus",
    "consumer-high-dj-tier": "dj",
}

# How to rebuild each type from its JSON form. Matched exactly, since a datetime is also a date.
_LOADERS: dict[type, Callable[[Any], Any]] = {
    datetime: datetime.fromisoformat,
    date: date.fromisoformat,
    Path: Path,
    float: float,
}


class Model:
    """Base for the dataclasses below. Converts them to JSON-safe dicts and back."""

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a dict that json.dumps accepts."""
        return {f.name: _dump(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild from `to_dict` output. Missing keys get defaults; unknown keys are ignored."""
        types = field_types(cls)
        return cls(
            **{name: _load(hint, data[name]) for name, hint in types.items() if name in data}
        )


@functools.cache
def field_types(cls: type) -> dict[str, Any]:
    """Each dataclass field of `cls` with its resolved type hint."""
    hints = get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


def _dump(value: Any) -> Any:
    """Turn one field value into its JSON form."""
    if isinstance(value, Model):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    # This covers datetime too, since it's a subclass of date.
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, list | tuple):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _load(hint: Any, value: Any) -> Any:
    """Turn a JSON value back into the type its field declares."""
    if value is None:
        return None
    origin = get_origin(hint)
    if origin is UnionType or origin is Union:
        return _load(optional_type(hint), value)
    if origin is list:
        (item_type,) = get_args(hint)
        return [_load(item_type, item) for item in value]
    if origin is dict:
        _, item_type = get_args(hint)
        return {key: _load(item_type, item) for key, item in value.items()}
    # Literal values are kept as they are.
    if not isinstance(hint, type):
        return value
    if issubclass(hint, Model):
        return hint.from_dict(value)
    if issubclass(hint, Enum):
        return hint(value)
    loader = _LOADERS.get(hint)
    return loader(value) if loader else value


def optional_type(hint: Any) -> Any:
    """The X in `X | None`. Models use no other kind of union."""
    options = [arg for arg in get_args(hint) if arg is not NoneType]
    if len(options) != 1:
        raise TypeError(f"Models only support `X | None` unions, not {hint}")
    return options[0]


class JobState(StrEnum):
    """Where a job is: queued → resolving → needs_review → saving → saved, or skipped or error.

    The background download isn't a state. `Job.progress` tracks it while it runs, and
    `Job.staged_audio` is set when it's done.
    """

    queued = "queued"
    resolving = "resolving"
    needs_review = "needs_review"
    saving = "saving"
    saved = "saved"
    skipped = "skipped"
    error = "error"


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


@dataclass
class Change(Model):
    """One automatic cleanup, shown as a toggle."""

    id: str
    kind: ChangeKind
    span: str  # the exact text it acts on, such as "[prod. Hazy]"
    label: str  # the text shown on the toggle
    enabled: bool = True


@dataclass
class TagSuggestion(Model):
    """The suggested tags in structured form, before a title style renders them."""

    base_title: str
    main_artists: list[str]
    featured: list[str] = field(default_factory=list)
    version: str | None = None  # kept parts such as " (VIP Mix)"
    album: str | None = None
    album_artist: str | None = None
    year: int | None = None
    genre: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    changes: list[Change] = field(default_factory=list)


@dataclass
class Tags(Model):
    """Exactly what gets written to the file."""

    title: str
    artist: str
    album: str
    album_artist: str
    year: int | None = None
    genre: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    comment: str | None = None
    source_url: str | None = None
    source_id: str | None = None
    isrc: str | None = None
    label: str | None = None


@dataclass
class CropBox(Model):
    """A crop in fractions of the source image's width and height, so it survives resizing."""

    left: float
    top: float
    width: float
    height: float


@dataclass
class ArtworkChoice(Model):
    """Where the cover comes from and how to crop it."""

    kind: ArtworkKind = "source"
    value: str | None = None  # the URL or the file path; unused for "source" and "none"
    crop: CropBox | None = None  # None means a centered square


@dataclass
class Progress(Model):
    """How far a download has got."""

    pct: float | None = None  # 0 to 100; None while the size is unknown
    speed: float | None = None  # bytes per second
    eta: float | None = None  # seconds left
    downloaded: int | None = None  # bytes
    total: int | None = None  # bytes


@dataclass
class JobError(Model):
    """Why a job stopped."""

    kind: ErrorKind
    detail: str | None = None


@dataclass
class HistoryEntry(Model):
    """One saved file, as the history keeps it."""

    source_id: str | None
    url_norm: str | None
    saved_path: Path
    title: str
    artist: str
    album: str
    format: str  # the saved file's format, such as mp3
    bitrate: int | None = None  # kbps
    saved_at: datetime | None = None  # filled in when it's added
    id: int | None = None  # filled in by the database


@dataclass
class Job(Model):
    """One queue item: a track, a set or a file to re-tag."""

    id: str
    kind: JobKind
    input: str  # the link or file path as it was given
    state: JobState = JobState.queued
    source: SourceInfo | None = None  # None until resolved
    suggestion: TagSuggestion | None = None  # None until resolved
    overrides: dict[str, str] = field(default_factory=dict)  # fields the user typed by hand
    style: TitleStyle | None = None  # this track's title style, when it differs from the setting
    artwork: ArtworkChoice = field(default_factory=ArtworkChoice)
    staged_audio: Path | None = None
    output_path: Path | None = None
    error: JobError | None = None
    progress: Progress | None = None
    duplicate: HistoryEntry | None = None  # an earlier save of the same source
    children: list["Job"] = field(default_factory=list)  # a set's tracks
    # None lets the set type decide between saving as an album and as singles.
    album_mode: AlbumMode | None = None
    included: bool = True  # whether this track of a set gets saved


@dataclass
class Account(Model):
    """The signed-in SoundCloud account."""

    username: str
    permalink: str
    avatar_url: str | None = None
    tier: AccountTier = "free"


def account_tier(product_id: str | None) -> AccountTier:
    """The plan for a SoundCloud product id. An id we don't know is logged and counts as free."""
    tier = PRODUCT_TIERS.get(product_id or "")
    if tier is None:
        log.warning("Unknown SoundCloud product id %r, so the account counts as free", product_id)
        return "free"
    return tier


@dataclass
class Settings(Model):
    """Every option in the options menu with its default, plus first-run progress."""

    # Library
    library_dir: Path = field(default_factory=default_library_dir)
    # None means the title style's own default: {title} for all_in_title, else {artist} - {title}.
    filename_template: str | None = None
    when_file_exists: CollisionPolicy = "ask"
    artist_folders: bool = True
    album_subfolders: bool = False
    # Title style. First-run setup asks for the style, so its default only applies until then.
    title_style: TitleStyle = "artist_field"
    custom_title_template: str = "{title}{version}"
    custom_artist_template: str = "{artists}"
    artist_separator: ArtistSeparator = ", "
    feat_wording: FeatWording = "feat."
    remove_producer_credits: bool = True
    remove_promo_text: bool = True
    custom_words: list[str] = field(default_factory=list)
    singles_album: SinglesAlbum = "title"
    singles_album_text: str = ""  # used when singles_album is "fixed"
    # Audio
    audio_format: AudioFormat = "mp3_320"
    keep_lossless_originals: bool = False
    # Cover art
    cover_max_size: CoverSize = 1000
    save_cover_jpg: bool = False
    # Tags
    comment_mode: CommentMode = "source_url"
    # Workflow
    review_before_saving: bool = True
    download_while_reviewing: bool = True
    offer_clipboard_links: bool = True
    concurrent_downloads: DownloadsAtOnce = 2
    # Engine and updates
    check_app_updates: bool = True
    check_engine_updates: bool = True
    engine_channel: EngineChannel = "stable"
    soundcloud_client_id: str | None = None  # overrides the one yt-dlp finds
    # Not in the menu: set when first-run setup ends, so it isn't shown again.
    first_run_done: bool = False
