"""Rendering a suggestion in a title style: the presets, custom templates, the artist separator
and the feat. wording. Filenames are rendered by the library rules, not here."""

import re
from collections.abc import Mapping

from scmp3.core.models import Settings, Tags, TagSuggestion, TitleStyle
from scmp3.core.suggest.rules import tidy

# The tokens a template may use. Anything else in braces is left as it is.
_TOKEN = re.compile(r"\{(title|version|main|featured|artists)\}")
# A bracketed part of a template, innermost first, with the space before it.
_GROUP = re.compile(r"\s*(?:\([^()\[\]]*\)|\[[^()\[\]]*\])")
_BRACKET = re.compile(r"[()\[\]]")
_WHOLE_NUMBER = re.compile("[0-9]+")
# A hand-typed track number, optionally with the total: "3", "3/12" or "3 / 12".
_TRACK = re.compile(r"([0-9]+)(?:\s*/\s*([0-9]+))?")

# Title and artist templates for each preset. {feat} becomes the feat. wording setting.
PRESETS: dict[str, tuple[str, str]] = {
    "artist_field": ("{title}{version}", "{artists}"),
    "feat_in_title": ("{title} ({feat} {featured}){version}", "{main}"),
    "all_in_title": ("{title} ({artists}){version}", "{main}"),
}


class InvalidOverride(ValueError):
    """A hand-typed number field holds something that isn't a number."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(f"unusable {field}: {value!r}")
        self.field = field
        self.value = value


def render(
    suggestion: TagSuggestion,
    style: TitleStyle | None,
    overrides: Mapping[str, str],
    settings: Settings,
) -> Tags:
    """The tags to write, in `style` or else the style setting. Hand-typed overrides always win."""
    title_template, artist_template = templates(style or settings.title_style, settings)
    values = token_values(suggestion, settings.artist_separator)
    # With no title text, the title stays empty rather than becoming just "(Kaia)".
    has_title = bool(values["title"] or values["version"])
    title = overrides.get("title", fill(title_template, values) if has_title else "")
    first_main = suggestion.main_artists[0] if suggestion.main_artists else ""
    track_no, track_total = _track(overrides, suggestion)
    return Tags(
        title=title,
        artist=overrides.get("artist", fill(artist_template, values)),
        album=overrides.get("album", _album(suggestion, overrides, settings)),
        album_artist=overrides.get("album_artist", suggestion.album_artist or first_main),
        year=_number(overrides, "year", suggestion.year),
        genre=_text_or_none(overrides, "genre", suggestion.genre),
        track_no=track_no,
        track_total=track_total,
    )


def templates(style: TitleStyle, settings: Settings) -> tuple[str, str]:
    """The title and artist templates for a style."""
    if style == "custom":
        return settings.custom_title_template, settings.custom_artist_template
    title, artist = PRESETS[style]
    return title.replace("{feat}", settings.feat_wording), artist


def token_values(suggestion: TagSuggestion, separator: str) -> dict[str, str]:
    """What each template token stands for."""
    main, featured = suggestion.main_artists, suggestion.featured
    return {
        "title": suggestion.base_title,
        "version": suggestion.version or "",
        "main": separator.join(main),
        "featured": separator.join(featured),
        "artists": separator.join([*main, *featured]),
    }


def fill(template: str, values: Mapping[str, str]) -> str:
    """Fill in a template. Empty parts collapse, so no stray "()" or "feat." is left behind."""
    pieces = _TOKEN.split(_drop_empty_groups(template, values))
    # Pieces alternate literal text and token names, starting and ending with literal text.
    out = [pieces[0]]
    for index, (name, literal) in enumerate(zip(pieces[1::2], pieces[2::2], strict=True)):
        value = values[name]
        before = out[-1]
        joiner = _after_last_bracket(before)
        if value:
            out.extend([value, literal])
        elif joiner.strip():
            # An empty token takes the words before it along, like "feat." in "feat. {featured}".
            out[-1] = before[: len(before) - len(joiner)]
            out.extend(["", literal])
        elif index == 0 or before.rstrip().endswith(("(", "[")):
            # At the start, it takes the joiner after it, like " x " in "{featured} x {main}".
            out.extend(["", _from_first_bracket(literal)])
        else:
            out.extend(["", literal])
    return tidy("".join(out))


def _drop_empty_groups(template: str, values: Mapping[str, str]) -> str:
    """Remove each bracketed part whose tokens are all empty, innermost first."""

    def drop_if_empty(match: re.Match[str]) -> str:
        tokens = _TOKEN.findall(match.group())
        if tokens and not any(values[token] for token in tokens):
            return ""
        return match.group()

    while True:
        shorter = _GROUP.sub(drop_if_empty, template)
        if shorter == template:
            return template
        template = shorter


def _after_last_bracket(literal: str) -> str:
    """The part of a literal after its last bracket, or all of it when it has none."""
    brackets = list(_BRACKET.finditer(literal))
    return literal[brackets[-1].end() :] if brackets else literal


def _from_first_bracket(literal: str) -> str:
    """The part of a literal from its first bracket on, or nothing when it has none."""
    bracket = _BRACKET.search(literal)
    return literal[bracket.start() :] if bracket else ""


def _album(suggestion: TagSuggestion, overrides: Mapping[str, str], settings: Settings) -> str:
    """The suggestion's album, or else what the album-for-singles setting asks for.

    "Same as the title" means the title without its artists, in every style: the base title and
    version, or the title typed by hand.
    """
    if suggestion.album is not None:
        return suggestion.album
    title = overrides.get("title", "").strip() or tidy(
        suggestion.base_title + (suggestion.version or "")
    )
    choices = {"title": title, "fixed": settings.singles_album_text, "blank": ""}
    return choices[settings.singles_album]


def _text_or_none(overrides: Mapping[str, str], field: str, default: str | None) -> str | None:
    """A text field that may be empty. A blank override clears it."""
    if field not in overrides:
        return default
    return overrides[field].strip() or None


def _number(overrides: Mapping[str, str], field: str, default: int | None) -> int | None:
    """A number field. A blank override clears it; anything but digits is refused."""
    if field not in overrides:
        return default
    text = overrides[field].strip()
    if not text:
        return None
    if not _WHOLE_NUMBER.fullmatch(text):
        raise InvalidOverride(field, text)
    return int(text)


def _track(
    overrides: Mapping[str, str], suggestion: TagSuggestion
) -> tuple[int | None, int | None]:
    """The track number and total. A typed "3/12" fills in the total too, unless it's typed."""
    number, total = suggestion.track_no, suggestion.track_total
    typed = overrides.get("track_no", "").strip()
    if typed:
        match = _TRACK.fullmatch(typed)
        if match is None:
            raise InvalidOverride("track_no", typed)
        number = int(match[1])
        total = int(match[2]) if match[2] else total
    elif "track_no" in overrides:
        number = None
    return number, _number(overrides, "track_total", total)
