"""The flow from a link to a saved file, shared by the CLI and the app.

`prepare` resolves a link and suggests tags, `fetch` downloads and converts the audio in a staging
folder, and `finalize` tags the file and moves it into the library. A set is listed with
`prepare_set`, and each of its tracks resolves with `prepare_set_track`. `album_places` numbers an
album's tracks and loads their shared cover, and `finalize_set_track` saves one track, which
`finalize_set` does for the whole set in order. They're plain functions; the app runs them on its
worker threads. Callers use the names below, not the stage modules.
"""

from scmp3.core.pipeline.albums import (
    album_cover_source,
    album_fields,
    album_overrides,
    render_album_track,
    track_numbers,
)
from scmp3.core.pipeline.common import (
    Prepared,
    PreparedSet,
    Saved,
    SetTrack,
    Staged,
    Tools,
    TrackOutcome,
    TrackResult,
    YdlMaker,
    discard,
    expected_duration,
)
from scmp3.core.pipeline.downloading import STAGED_STEM, fetch
from scmp3.core.pipeline.resolving import (
    FILES_NOT_SUPPORTED,
    NOT_YET,
    RESOLVERS,
    SETS_NOT_SUPPORTED,
    YOUTUBE_NOT_SUPPORTED,
    Resolver,
    find_duplicate,
    prepare,
    prepare_set,
    prepare_set_track,
    prepare_set_tracks,
    unsupported_reason,
)
from scmp3.core.pipeline.saving import LoadedCover, destination, finalize
from scmp3.core.pipeline.saving_sets import (
    AlbumPlace,
    album_places,
    finalize_set,
    finalize_set_track,
)
from scmp3.core.pipeline.tags import COMMENTS, NUMBER_RANGES, render_tags

__all__ = [
    "COMMENTS",
    "FILES_NOT_SUPPORTED",
    "NOT_YET",
    "NUMBER_RANGES",
    "RESOLVERS",
    "SETS_NOT_SUPPORTED",
    "STAGED_STEM",
    "YOUTUBE_NOT_SUPPORTED",
    "AlbumPlace",
    "LoadedCover",
    "Prepared",
    "PreparedSet",
    "Resolver",
    "Saved",
    "SetTrack",
    "Staged",
    "Tools",
    "TrackOutcome",
    "TrackResult",
    "YdlMaker",
    "album_cover_source",
    "album_fields",
    "album_overrides",
    "album_places",
    "destination",
    "discard",
    "expected_duration",
    "fetch",
    "finalize",
    "finalize_set",
    "finalize_set_track",
    "find_duplicate",
    "prepare",
    "prepare_set",
    "prepare_set_track",
    "prepare_set_tracks",
    "render_album_track",
    "render_tags",
    "track_numbers",
    "unsupported_reason",
]
