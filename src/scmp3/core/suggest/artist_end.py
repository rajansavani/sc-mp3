"""Step 4 in "Title - Artist" order: the part after the title's last dash names the uploader or a
listed artist, as in "Numb - Linkin Park" from Linkin Park or "dư âm - Obito" listing Obito."""

from scmp3.core.suggest.draft import SEPARATORS, Context, Draft, has_words, spaced_dashes
from scmp3.core.suggest.names import same_artist, strip_invisible


def claim_artist_at_end(draft: Draft, context: Context) -> None:
    """Take the name after the title's last dash when it's the uploader or a listed artist.

    The uploader's own name is dropped, and a listed artist is credited. The claim takes the dash
    too, so text kept after the name never follows a bare dash. A title whose first part already
    names one of them is in "Artist - Title" order, so its end is the song's own.
    """
    masked = draft.masked()
    dashes = spaced_dashes(masked)
    if not dashes or not has_words(masked[: dashes[-1]]):
        return
    start, end = _trimmed(masked, dashes[-1] + 1, len(masked))
    name = strip_invisible(masked[start:end])
    if not name or _names_an_artist(masked[: dashes[0]], context):
        return
    dash = dashes[-1]
    if same_artist(name, context.uploader):
        draft.claim("strip_redundant_artist", dash, end)
    elif any(same_artist(name, listed) for listed in context.publisher):
        draft.claim("artist_from_title", dash, end, names=(name,))


def _names_an_artist(text: str, context: Context) -> bool:
    """Whether `text`, as a whole, is the uploader or a listed artist."""
    start, end = _trimmed(text, 0, len(text))
    part = strip_invisible(text[start:end])
    return bool(part) and any(
        same_artist(part, name) for name in [context.uploader, *context.publisher]
    )


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    """`start` and `end` moved inward past spaces and separators."""
    while start < end and text[start] in SEPARATORS:
        start += 1
    while end > start and text[end - 1] in SEPARATORS:
        end -= 1
    return start, end
