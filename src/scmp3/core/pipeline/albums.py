"""What the tracks of a set saved as an album share: the album fields, numbers among the included
tracks, and one cover (TECH_SPEC §6)."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from scmp3.core.models import PlaylistInfo, Settings, SourceInfo, Tags, TagSuggestion, TitleStyle
from scmp3.core.pipeline.tags import render_tags
from scmp3.core.suggest.names import same_artist


def album_fields(playlist: PlaylistInfo, tracks: Sequence[TagSuggestion] = ()) -> dict[str, str]:
    """What every track of the album shares, as overrides: album, album artist, year and genre.

    `tracks` are the included tracks' suggestions, in order. The album is the set's title. The
    album artist is the main artist every track shares, so a label's upload of one artist's album
    files under the artist, and otherwise it's the set's owner. Year and genre are the set's, or
    else the first track's, and one that neither has is left out.
    """
    first = tracks[0] if tracks else None
    fields = {
        "album": playlist.title.strip() or (first.album if first else None) or "",
        "album_artist": _shared_main_artist(tracks) or playlist.owner.strip(),
    }
    year = playlist.release_date.year if playlist.release_date else None
    genre = _tidy_genre(playlist.genre)
    if first is not None:
        year = year or first.year
        genre = genre or first.genre
    if year is not None:
        fields["year"] = str(year)
    if genre:
        fields["genre"] = genre
    return fields


def album_overrides(album: Mapping[str, str], track_overrides: Mapping[str, str]) -> dict[str, str]:
    """What a track of the album is tagged and placed with: the album's fields under its own edits.

    Pass the result to both render_album_track and destination, so the tags and the folder agree.
    """
    return {**album, **track_overrides}


def track_numbers(included: Sequence[bool]) -> list[int | None]:
    """Each track's number among the included ones, counting from 1, or None for one left out."""
    numbers: list[int | None] = []
    count = 0
    for is_included in included:
        if is_included:
            count += 1
        numbers.append(count if is_included else None)
    return numbers


def album_cover_source(
    playlist: PlaylistInfo, first_track: SourceInfo
) -> PlaylistInfo | SourceInfo:
    """Whose cover the album shares: the set's as listed, or else the first track that resolved.

    The set's listed cover is its own, or its first track's when it has none.
    """
    return playlist if playlist.artwork_url else first_track


def render_album_track(
    source: SourceInfo,
    suggestion: TagSuggestion,
    settings: Settings,
    *,
    track_no: int,
    track_total: int,
    overrides: Mapping[str, str],
    style: TitleStyle | None = None,
) -> Tags:
    """The tags of a track saved in an album, with its number. `overrides` are album_overrides'.

    Raises InvalidOverride as render_tags does.
    """
    numbered = replace(suggestion, track_no=track_no, track_total=track_total)
    return render_tags(source, numbered, settings, style=style, overrides=overrides)


def _shared_main_artist(tracks: Sequence[TagSuggestion]) -> str | None:
    """The main artist every track names first, spelled as the first track has it, or None."""
    names = [track.main_artists[0] if track.main_artists else "" for track in tracks]
    if not names or not names[0]:
        return None
    if all(same_artist(name, names[0]) for name in names[1:]):
        return names[0]
    return None


def _tidy_genre(genre: str | None) -> str | None:
    """SoundCloud's genre without a leading #, as the suggestion has a track's."""
    return (genre or "").strip().lstrip("#").strip() or None
