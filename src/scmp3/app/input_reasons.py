"""The codes add_inputs sends for rejected items: each reason's constant name in core.

Core keeps the reasons as text for the CLI. The UI gets the constant's name instead, and
web/strings/main.js has the text for each one.
"""

import logging
from types import ModuleType

from scmp3.core import inputs, pipeline, urls

# A link that's queued already. The answer names the job, so the UI can select it.
ALREADY_IN_QUEUE = "ALREADY_IN_QUEUE"

# The reasons each core module gives, by their constant names. A new reason is one more name.
REASONS_BY_MODULE: dict[ModuleType, tuple[str, ...]] = {
    urls: (
        "NOT_SUPPORTED",
        "SOUNDCLOUD_NOT_TRACK",
        "SHORT_LINK_DEAD_END",
        "YOUTUBE_PLAYLIST",
        "YOUTUBE_NOT_VIDEO",
    ),
    inputs: ("FILE_NOT_FOUND", "FOLDER_NOT_FILE", "NOT_RETAGGABLE", "PASTE_TOO_LONG"),
    pipeline: ("SETS_NOT_SUPPORTED",),
}

REASON_CODES: dict[str, str] = {
    getattr(module, name): name for module, names in REASONS_BY_MODULE.items() for name in names
}

log = logging.getLogger(__name__)


def reason_code(reason: str) -> str:
    """The code for a reason core gave. One without a code is logged, and sent as NOT_SUPPORTED."""
    code = REASON_CODES.get(reason)
    if code is None:
        log.warning(
            "A rejected item's reason has no code, so it's sent as NOT_SUPPORTED: %s", reason
        )
        return "NOT_SUPPORTED"
    return code
