"""The suggested tags with the cleanups behind them, and the tags that get written."""

from dataclasses import dataclass, field
from typing import Literal

from scmp3.core.models.base import Model

ChangeKind = Literal[
    "remove_promo",
    "remove_producer",
    "credit_producer",
    "extract_feature",
    "artist_from_title",
    "strip_redundant_artist",
    "remove_emoji",
    "fix_caps",
    "custom_word",
]


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
