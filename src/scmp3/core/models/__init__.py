"""The data passed between core, the app and the UI, and the settings.

Every model converts to a JSON-safe dict and back, for the bridge and queue.json. Each area has
its own module; import the names from here.
"""

from scmp3.core.models.account import PRODUCT_TIERS, Account, AccountTier, account_tier
from scmp3.core.models.base import Model, field_types, optional_type
from scmp3.core.models.jobs import (
    AlbumMode,
    ArtworkChoice,
    ArtworkKind,
    CropBox,
    HistoryEntry,
    Job,
    JobError,
    JobKind,
    JobState,
    Progress,
)
from scmp3.core.models.settings import (
    ArtistSeparator,
    AudioFormat,
    CollisionPolicy,
    CommentMode,
    CoverSize,
    DownloadsAtOnce,
    EngineChannel,
    FeatWording,
    Settings,
    SinglesAlbum,
    TitleStyle,
)
from scmp3.core.models.sources import (
    Availability,
    FormatChoice,
    FormatOption,
    OriginalDownload,
    PlaylistEntry,
    PlaylistInfo,
    SourceInfo,
    SourceKind,
)
from scmp3.core.models.tags import Change, ChangeKind, Tags, TagSuggestion

__all__ = [
    "PRODUCT_TIERS",
    "Account",
    "AccountTier",
    "AlbumMode",
    "ArtistSeparator",
    "ArtworkChoice",
    "ArtworkKind",
    "AudioFormat",
    "Availability",
    "Change",
    "ChangeKind",
    "CollisionPolicy",
    "CommentMode",
    "CoverSize",
    "CropBox",
    "DownloadsAtOnce",
    "EngineChannel",
    "FeatWording",
    "FormatChoice",
    "FormatOption",
    "HistoryEntry",
    "Job",
    "JobError",
    "JobKind",
    "JobState",
    "Model",
    "OriginalDownload",
    "PlaylistEntry",
    "PlaylistInfo",
    "Progress",
    "Settings",
    "SinglesAlbum",
    "SourceInfo",
    "SourceKind",
    "TagSuggestion",
    "Tags",
    "TitleStyle",
    "account_tier",
    "field_types",
    "optional_type",
]
