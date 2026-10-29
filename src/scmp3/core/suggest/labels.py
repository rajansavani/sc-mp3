"""The ids of the changes, and the text on each change's toggle in the review screen, in the
design's wording."""

from collections.abc import Sequence

from scmp3.core.suggest.draft import ARTIST_KINDS, SEPARATORS
from scmp3.core.suggest.names import any_same_artist, comma_parts

# Each kind's label. {span} is the exact text the change acts on.
LABELS: dict[str, str] = {
    "remove_promo": "Removed “{span}”",
    "remove_producer": "Removed “{span}”",
    "credit_producer": "Credited the producer {span} as a featured artist",
    "custom_word": "Removed “{span}”",
    "remove_emoji": "Removed “{span}”",
    "artist_from_title": "Took the artist “{span}” from the title",
    "strip_redundant_artist": "Removed the artist name “{span}” from the title",
    "fix_caps": "Changed “{span}” to title case",
}


def new_change_id(kind: str, span: str, taken: dict[str, int]) -> str:
    """A change id made from its kind and text, so it stays the same when other changes flip.

    `taken` counts the ids used so far, so a second change on the same text gets "#2".
    """
    base = f"{kind}:{span}"
    taken[base] = taken.get(base, 0) + 1
    return base if taken[base] == 1 else f"{base}#{taken[base]}"


def change_label(kind: str, span: str, names: Sequence[str] = ()) -> str:
    """The label for one change."""
    if kind == "extract_feature":
        noun = "artist" if len(names) == 1 else "artists"
        return f"{_list_names(names)} credited as featured {noun}"
    return LABELS[kind].format(span=shown_text(kind, span))


def shown_text(kind: str, span: str) -> str:
    """The text a change names in its id and label.

    An artist at the end of a title is claimed with the dash before it, as "- Kaia", and named
    without it, so its id is the one it had before the claim took the dash.
    """
    return span.strip(SEPARATORS) if kind in ARTIST_KINDS else span


def feature_label(
    span: str, names: Sequence[str], main: Sequence[str], featured: Sequence[str]
) -> str:
    """A feature's label, saying which of its names are main artists already.

    In "Kaia - Song (feat. Kaia)", or when "Rival, Cadmium, Harley Bird" is one main artist, the
    feature only removes its text.
    """
    listed = [*main, *(part for name in main for part in comma_parts(name))]
    already = [
        name
        for name in names
        if not any_same_artist([name], featured) and any_same_artist([name], listed)
    ]
    if not already:
        return change_label("extract_feature", span, names)
    being = "is already a main artist" if len(already) == 1 else "are already main artists"
    known = f"{_list_names(already)} {being}."
    others = [name for name in names if name not in already]
    if not others:
        return f"Removed “{span}”. {known}"
    return f"{change_label('extract_feature', span, others)}. {known}"


def _list_names(names: Sequence[str]) -> str:
    """ "Lou V", "Lou V and Kaia", or "A, B and C"."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
