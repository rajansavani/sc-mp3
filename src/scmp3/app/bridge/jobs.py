"""Bridge calls for the queue: adding, editing, saving and sets, plus history and diagnostics.

Each call checks its arguments, hands the work to the job manager and returns at once. An
argument the UI should never send is logged and answered with `internal`, since it's a bug.
"""

import logging
import math
import os
from collections.abc import Callable, Mapping
from typing import Any, cast, get_args

from scmp3 import __version__
from scmp3.app.clipboard import offered_link
from scmp3.app.components_view import components_view
from scmp3.app.context import AppContext
from scmp3.app.diagnostics import diagnostics_text
from scmp3.app.job_fields import EDITABLE_FIELDS
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import CollisionChoice
from scmp3.core.log import LOG_FILE
from scmp3.core.models import AlbumMode, CropBox, TitleStyle

INTERNAL = ErrorKind.internal.value
INVALID_ARTWORK = "invalid_artwork"

# Bigger arguments than these can only be a bug, or something pasted by mistake.
MAX_ID_LENGTH = 64
MAX_INPUT_CHARACTERS = 2_000_000  # parse_inputs reads the first 1,000,000
MAX_FIELD_CHARACTERS = 2_000
MAX_LINK_CHARACTERS = 8_000
# A 20 MB image, which the UI allows, is about 27 MB as base64.
MAX_DATA_URL_CHARACTERS = 30_000_000
MAX_QUERY_CHARACTERS = 1_000
MAX_HISTORY_LIMIT = 500

COLLISION_CHOICES = get_args(CollisionChoice)
ALBUM_MODES = get_args(AlbumMode)
CROP_SIDES = ("left", "top", "width", "height")
# How far a crop may reach past the image's edge, for rounding in the UI.
CROP_SLACK = 1e-6

log = logging.getLogger(__name__)


def refused(call: str) -> dict[str, Any]:
    """The answer to arguments the UI should never send, logged so the bug can be found."""
    log.warning("The UI called %s with arguments it can't use", call)
    return {"error": INTERNAL}


def is_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID_LENGTH


def input_text(value: Any) -> str | None:
    """Pasted text as it is, or dropped paths one per line, as parse_inputs reads them."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        value = "\n".join(value)
    if isinstance(value, str) and len(value) <= MAX_INPUT_CHARACTERS:
        return value
    return None


def is_patch(value: Any) -> bool:
    """Whether a patch holds only fields the review form edits, each as text."""
    if not isinstance(value, dict) or not value:
        return False
    return all(
        field in EDITABLE_FIELDS and isinstance(text, str) and len(text) <= MAX_FIELD_CHARACTERS
        for field, text in value.items()
    )


def crop_box(value: Any) -> CropBox | None:
    """A crop box from the UI, or None when it isn't a usable one: fractions inside the image."""
    if not isinstance(value, Mapping) or set(value) != set(CROP_SIDES):
        return None
    numbers = [value[side] for side in CROP_SIDES]
    if not all(_is_number(number) for number in numbers):
        return None
    left, top, width, height = numbers
    inside = left >= 0 and top >= 0 and width > 0 and height > 0
    if not inside or left + width > 1 + CROP_SLACK or top + height > 1 + CROP_SLACK:
        return None
    return CropBox(left=left, top=top, width=width, height=height)


def _is_number(value: Any) -> bool:
    # bool is an int too, and JSON can't send NaN, but a bug in Python's own callers could.
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_https_link(value: str) -> bool:
    return value[:8].lower() == "https://" and len(value) <= MAX_LINK_CHARACTERS


def _is_data_url(value: str) -> bool:
    return value[:5].lower() == "data:" and len(value) <= MAX_DATA_URL_CHARACTERS


def _is_full_path(value: str) -> bool:
    return len(value) <= MAX_LINK_CHARACTERS and os.path.isabs(value)


# What each kind of cover needs as its value. The source's own cover takes none.
ARTWORK_VALUE_CHECKS: dict[str, Callable[[str], bool]] = {
    "url": _is_https_link,
    "data_url": _is_data_url,
    "file": _is_full_path,
}


def fits_kind(kind: Any, value: Any) -> bool:
    """Whether an artwork choice's value suits its kind."""
    if kind == "source":
        return True
    check = ARTWORK_VALUE_CHECKS.get(kind) if isinstance(kind, str) else None
    return check is not None and isinstance(value, str) and bool(value) and check(value)


class JobsApi:
    """The queue's calls. Each returns right away; progress arrives as events."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """The queue's part of `snapshot()`."""
        return {"jobs": self._context.jobs.views()}

    # Inputs

    def add_inputs(self, text_or_paths: str | list[str]) -> dict[str, Any]:
        """Queue pasted text or dropped paths. Returns the new job ids and what was rejected."""
        text = input_text(text_or_paths)
        if text is None:
            return refused("add_inputs")
        return self._context.jobs.add_inputs(text)

    def clipboard_link(self) -> dict[str, Any]:
        """The link on the clipboard worth offering: one sc-mp3 can save that isn't queued yet."""
        context = self._context
        if not context.settings.load().offer_clipboard_links:
            return {"link": None}
        text = context.clipboard()
        return {"link": offered_link(text, context.jobs.is_queued) if text else None}

    # Editing

    def update_fields(self, job_id: str, patch: dict[str, str]) -> dict[str, Any]:
        """Set fields the user typed by hand, which win over the suggestion."""
        if not is_id(job_id) or not is_patch(patch):
            return refused("update_fields")
        return self._context.jobs.update_fields(job_id, patch)

    def reset_field(self, job_id: str, field: str) -> dict[str, Any]:
        """Drop a hand edit, so the field follows the suggestion again."""
        if not is_id(job_id) or field not in EDITABLE_FIELDS:
            return refused("reset_field")
        return self._context.jobs.reset_field(job_id, field)

    def toggle_change(self, job_id: str, change_id: str, enabled: bool) -> dict[str, Any]:
        """Turn one automatic cleanup on or off."""
        # A change's id holds the text it acts on, so it's as long as that text.
        valid_change = isinstance(change_id, str) and bool(change_id)
        if not is_id(job_id) or not valid_change or not isinstance(enabled, bool):
            return refused("toggle_change")
        return self._context.jobs.toggle_change(job_id, change_id, enabled)

    def set_style(self, job_id: str, style_id: str) -> dict[str, Any]:
        """Use a different title style for this track."""
        if not is_id(job_id) or style_id not in get_args(TitleStyle):
            return refused("set_style")
        return self._context.jobs.set_style(job_id, cast(TitleStyle, style_id))

    def set_artwork(
        self, job_id: str, artwork: dict[str, str], crop: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """Replace the cover with a file, a URL or a pasted image, with an optional square crop."""
        if not is_id(job_id) or not isinstance(artwork, dict):
            return refused("set_artwork")
        kind, value = artwork.get("kind"), artwork.get("value")
        box = crop_box(crop) if crop is not None else None
        if not fits_kind(kind, value) or (crop is not None and box is None):
            log.info("A cover choice was refused: a %s cover", kind)
            return {"error": INVALID_ARTWORK}
        return self._context.jobs.set_artwork(
            job_id, kind, None if kind == "source" else value, box
        )

    # Actions

    def save(self, job_ids: list[str]) -> dict[str, Any]:
        """Tag the jobs and move them into the library."""
        if not isinstance(job_ids, list) or not all(is_id(job_id) for job_id in job_ids):
            return refused("save")
        return self._context.jobs.save(job_ids)

    def skip(self, job_id: str) -> dict[str, Any]:
        """Leave a job unsaved and delete its staged audio."""
        if not is_id(job_id):
            return refused("skip")
        return self._context.jobs.skip(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        """Start a failed job again."""
        if not is_id(job_id):
            return refused("retry")
        return self._context.jobs.retry(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Stop a job's download, or put a save that waits for one back in review."""
        if not is_id(job_id):
            return refused("cancel")
        return self._context.jobs.cancel(job_id)

    def remove(self, job_id: str) -> dict[str, Any]:
        """Take a job out of the queue."""
        if not is_id(job_id):
            return refused("remove")
        return self._context.jobs.remove(job_id)

    def remove_jobs(self, job_ids: list[str]) -> dict[str, Any]:
        """Take many jobs out of the queue in one call, as Clear finished does."""
        if not isinstance(job_ids, list) or not all(is_id(job_id) for job_id in job_ids):
            return refused("remove_jobs")
        return self._context.jobs.remove_jobs(job_ids)

    def answer_collision(
        self, job_id: str, choice: str, rest_of_set: bool = False
    ) -> dict[str, Any]:
        """Say what to do about a file with the same name: replace, keep both or skip. With
        `rest_of_set`, the answer goes for the other tracks of the same set too."""
        valid_choice = choice in COLLISION_CHOICES and isinstance(rest_of_set, bool)
        if not is_id(job_id) or not valid_choice:
            return refused("answer_collision")
        return self._context.jobs.answer_collision(
            job_id, cast(CollisionChoice, choice), rest_of_set
        )

    # Sets

    def set_album_mode(self, job_id: str, mode: str) -> dict[str, Any]:
        """Save a set as one album or as separate singles."""
        if not is_id(job_id) or mode not in ALBUM_MODES:
            return refused("set_album_mode")
        return self._context.jobs.set_album_mode(job_id, cast(AlbumMode, mode))

    def set_included(self, job_id: str, child_id: str, included: bool) -> dict[str, Any]:
        """Choose whether one track of a set gets saved."""
        if not is_id(job_id) or not is_id(child_id) or not isinstance(included, bool):
            return refused("set_included")
        return self._context.jobs.set_included(job_id, child_id, included)

    # Misc

    def history(self, query: str = "", limit: int = 50) -> dict[str, Any]:
        """Search the files saved before, newest first. An empty query lists the latest."""
        valid_query = isinstance(query, str) and len(query) <= MAX_QUERY_CHARACTERS
        valid_limit = isinstance(limit, int) and not isinstance(limit, bool)
        if not valid_query or not valid_limit or not 0 < limit <= MAX_HISTORY_LIMIT:
            return refused("history")
        try:
            entries = self._context.history.search(query, limit)
        except ScmpError as error:
            log.warning("The history couldn't be searched: %s", error)
            return {"error": INTERNAL}
        return {"entries": [entry.to_dict() for entry in entries]}

    def diagnostics(self) -> dict[str, Any]:
        """Versions, settings and recent log lines, with secrets and the home folder masked."""
        context = self._context
        components = components_view(context)
        text = diagnostics_text(
            app_version=__version__,
            engine_version=components["engine_version"],
            settings=context.settings.load(),
            log_file=paths.log_dir() / LOG_FILE,
            components=components,
        )
        return {"text": text}
