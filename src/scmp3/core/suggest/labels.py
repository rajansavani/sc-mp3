"""The text on each change's toggle in the review screen, in the design's wording."""

from collections.abc import Sequence

# Each kind's label. {span} is the exact text the change acts on.
LABELS: dict[str, str] = {
    "remove_promo": "Removed “{span}”",
    "remove_producer": "Removed “{span}”",
    "custom_word": "Removed “{span}”",
    "remove_emoji": "Removed “{span}”",
    "artist_from_title": "Took the artist “{span}” from the title",
    "strip_redundant_artist": "Removed the artist name “{span}” from the title",
    "fix_caps": "Changed “{span}” to title case",
}


def change_label(kind: str, span: str, names: Sequence[str] = ()) -> str:
    """The label for one change."""
    if kind == "extract_feature":
        noun = "artist" if len(names) == 1 else "artists"
        return f"{_list_names(names)} credited as featured {noun}"
    return LABELS[kind].format(span=span)


def _list_names(names: Sequence[str]) -> str:
    """ "Lou V", "Lou V and Kaia", or "A, B and C"."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
