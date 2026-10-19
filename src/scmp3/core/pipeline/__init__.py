"""The flow from a link to a saved file, shared by the CLI and the app.

`prepare` resolves a link and suggests tags, `fetch` downloads and converts the audio in a staging
folder, and `finalize` tags the file and moves it into the library. They're plain functions; the
app runs them on its worker threads. Callers use the names below, not the stage modules.
"""

from scmp3.core.pipeline.common import (
    Prepared,
    Saved,
    Staged,
    Tools,
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
    unsupported_reason,
)
from scmp3.core.pipeline.saving import destination, finalize
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
    "Prepared",
    "Resolver",
    "Saved",
    "Staged",
    "Tools",
    "YdlMaker",
    "destination",
    "discard",
    "expected_duration",
    "fetch",
    "finalize",
    "find_duplicate",
    "prepare",
    "render_tags",
    "unsupported_reason",
]
