"""Artist lists set by hand in the review form: the main and the featured names, in order.

A list set by hand wins over the cleanups, so every rebuilt suggestion gets it back. The album
artist follows its first main artist, since render takes the first main artist (TECH_SPEC §12).
Names are cleaned here, the one place the UI's lists and the saved tags both go through.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from scmp3.core.models import TagSuggestion
from scmp3.core.suggest.names import name_key, split_artist_list, strip_invisible
from scmp3.core.suggest.words import load_wordlists

# What save answers when a list has featured artists but no main one. web/strings has the text.
NO_MAIN_ARTIST = "no_main_artist"


@dataclass(frozen=True)
class ArtistList:
    """Who's credited on a track: main artists, then featured ones, each in order."""

    main: tuple[str, ...]
    featured: tuple[str, ...]

    @classmethod
    def of(cls, main: Any, featured: Any) -> "ArtistList":
        """A list from names as the UI sends them, cleaned as `cleaned_names` cleans them.

        Raises TypeError when the names aren't two lists of text.
        """
        rows = (_texts(main), _texts(featured))
        seen: set[str] = set()
        main_names, featured_names = (cleaned_names(names, seen) for names in rows)
        return cls(main=main_names, featured=featured_names)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtistList":
        """The list queue.json kept. Raises KeyError or TypeError, as `of` does."""
        return cls.of(data["main"], data["featured"])

    def to_dict(self) -> dict[str, list[str]]:
        return {"main": list(self.main), "featured": list(self.featured)}


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise TypeError("an artist list holds names as text")
    return value


def cleaned_names(names: Iterable[str], seen: set[str]) -> tuple[str, ...]:
    """The names without spaces or invisible characters at their edges, as T-033 trims names.

    A name that's left empty goes, and so does one already in `seen`, ignoring case, so a name
    is credited once across both rows. `seen` gains the names kept.
    """
    kept: list[str] = []
    for name in names:
        cleaned = strip_invisible(name)
        key = name_key(cleaned)
        if key and key not in seen:
            seen.add(key)
            kept.append(cleaned)
    return tuple(kept)


def pasted_names(text: str) -> list[str]:
    """The names in pasted text, split as SoundCloud's artist field is, one line or several.

    "gunk, zayok, edyn" gives three names, and a name in `[artists] whole_names`, like
    "Tyler, The Creator", stays whole.
    """
    joined = ", ".join(text.splitlines())
    return list(cleaned_names(split_artist_list(joined, load_wordlists().whole_names), set()))


def needs_main_artist(artists: ArtistList | None, overrides: Mapping[str, str]) -> bool:
    """Whether a list set by hand credits featured artists with no main one, so it can't be
    saved. A typed artist is the credit instead, and a list with no names at all saves as an
    empty artist does."""
    if artists is None or "artist" in overrides:
        return False
    return not artists.main and bool(artists.featured)


def with_artists(suggestion: TagSuggestion, artists: ArtistList | None) -> TagSuggestion:
    """The suggestion crediting `artists`, or as it is when no list was set by hand."""
    if artists is None:
        return suggestion
    return replace(suggestion, main_artists=list(artists.main), featured=list(artists.featured))
