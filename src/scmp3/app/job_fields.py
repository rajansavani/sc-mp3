"""Checks on what people type into the review form, before it reaches the tags.

Each refusal is a reason code, and web/strings/main.js has the text for it.
"""

import re
from collections.abc import Callable, Mapping

from scmp3.core.pipeline import NUMBER_RANGES
from scmp3.core.suggest import InvalidOverride

# The fields the review form edits. The track total is typed as "N/M" in track_no.
EDITABLE_FIELDS = ("title", "artist", "album", "album_artist", "year", "genre", "track_no")

NOT_A_YEAR = "NOT_A_YEAR"
NOT_A_TRACK = "NOT_A_TRACK"
TOTAL_BEFORE_TRACK = "TOTAL_BEFORE_TRACK"

# ASCII digits only, as the tag rendering accepts.
YEAR = re.compile(r"[0-9]{1,4}")
TRACK = re.compile(r"([0-9]{1,3})(?:\s*/\s*([0-9]{1,3}))?")

# The reason for each field render_tags can refuse, when it finds a problem these checks missed.
RENDER_REASONS = {"year": NOT_A_YEAR, "track_no": NOT_A_TRACK, "track_total": TOTAL_BEFORE_TRACK}

Problem = dict[str, str]


def year_problem(value: str) -> str | None:
    if YEAR.fullmatch(value) and int(value) in NUMBER_RANGES["year"]:
        return None
    return NOT_A_YEAR


def track_problem(value: str) -> str | None:
    match = TRACK.fullmatch(value)
    if match is None or int(match[1]) not in NUMBER_RANGES["track_no"]:
        return NOT_A_TRACK
    if match[2] is not None and int(match[2]) < int(match[1]):
        return TOTAL_BEFORE_TRACK
    return None


# The checks for fields that hold numbers. Text fields take anything.
CHECKS: dict[str, Callable[[str], str | None]] = {"year": year_problem, "track_no": track_problem}


def field_problems(patch: Mapping[str, str]) -> dict[str, Problem]:
    """Each typed value that can't be used, with why. A blank value clears its field."""
    problems: dict[str, Problem] = {}
    for field, value in patch.items():
        check = CHECKS.get(field)
        text = value.strip()
        reason = check(text) if check and text else None
        if reason:
            problems[field] = {"reason": reason}
    return problems


def render_problem(error: InvalidOverride) -> dict[str, Problem]:
    """The field message for a value render_tags refused, next to the field that was typed."""
    reason = RENDER_REASONS.get(error.field, NOT_A_TRACK)
    # The total is typed in the track field, and the smaller total may be the suggestion's own.
    field = "track_no" if error.field == "track_total" else error.field
    return {field: {"reason": reason}}
