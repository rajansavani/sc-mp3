"""The YouTube additions to the suggestion engine (TECH_SPEC §8): channel names as artist names,
the channel's own name at the end of a title, and the video labels that mark another's song."""

import re

from scmp3.core.models import SourceInfo
from scmp3.core.suggest.draft import Context, Draft, has_words
from scmp3.core.suggest.names import same_artist
from scmp3.core.suggest.rules import normalize
from scmp3.core.suggest.words import pattern

# YouTube names an artist's release channel "<artist> - Topic".
_TOPIC = re.compile(r"\s+-\s+Topic$")
# Label-run channels are "<artist>VEVO", usually with the artist's spaces taken out.
_VEVO = re.compile(r"(?<=\S)\s*VEVO$", re.IGNORECASE)
_TITLE_ARTIST = re.compile(r"\s-\s")
_SPACED_DASH = re.compile(r"(?<=\s)-(?=\s)")
# Trimmed off the part after the last dash, as dash_segments trims each part.
_END_TRIM = " -|/:"


def uploader_name(source: SourceInfo) -> str:
    """The uploader as the artist's name. For YouTube, the Topic and VEVO suffixes go.

    A VEVO channel's name, like "TaylorSwift" or "shakira", takes the title's own spelling when
    the title's "Artist - " part names the same artist. So does any name squashed without spaces.
    """
    if source.kind != "youtube":
        return source.uploader
    name = _TOPIC.sub("", source.uploader.strip())
    unsuffixed = _VEVO.sub("", name)
    if unsuffixed != name:
        return _spelled_as_in_title(unsuffixed, source.title, spaced_only=False)
    return _spelled_as_in_title(name, source.title, spaced_only=True)


def _spelled_as_in_title(name: str, title: str, *, spaced_only: bool) -> str:
    """The title's spelling of the name, when its "Artist - " part names the same artist.

    With `spaced_only`, only a spelling that puts spaces into a name without them counts.
    """
    if spaced_only and " " in name:
        return name
    whole = normalize(title)
    artist_part = _TITLE_ARTIST.split(whole, maxsplit=1)[0].strip()
    if artist_part == whole or (spaced_only and " " not in artist_part):
        return name
    return artist_part if same_artist(artist_part, name) else name


def is_youtube_without_music_fields(context: Context) -> bool:
    """Whether the source is a YouTube video that YouTube Music says nothing about."""
    return context.source.kind == "youtube" and not context.publisher


def claim_artist_at_end(draft: Draft, context: Context) -> None:
    """Drop the channel's own name after the title's last dash, as in "Numb - Linkin Park".

    The artist's channel names itself there, so the title before the dash is the song.
    """
    if not is_youtube_without_music_fields(context) or not context.uploader.strip():
        return
    masked = draft.masked()
    dashes = list(_SPACED_DASH.finditer(masked))
    if not dashes or not has_words(masked[: dashes[-1].start()]):
        return
    start, end = dashes[-1].end(), len(masked)
    while start < end and masked[start] in _END_TRIM:
        start += 1
    while end > start and masked[end - 1] in _END_TRIM:
        end -= 1
    if same_artist(masked[start:end], context.uploader):
        draft.claim("strip_redundant_artist", start, end)


def names_another_artist(draft: Draft, context: Context) -> bool:
    """Whether a YouTube title's "Artist - Title" is another artist's song, as channels label it.

    A video label such as "(Lyrics)" or "(Official Video)" marks a song posted by a lyrics or
    repost channel. Without one, "Interstellar - Main Theme" is more likely a title of its own.
    """
    if not is_youtube_without_music_fields(context):
        return False
    labels = pattern(context.words.video_labels)
    return any(
        claim.kind == "remove_promo" and labels.search(draft.span(claim)) for claim in draft.claims
    )
