"""Choose the SoundCloud format to download, by rules 1 to 4 of TECH_SPEC §6."""

from collections.abc import Sequence

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Availability, FormatChoice, FormatOption, SourceInfo

# The error each availability other than "ok" raises when a format is asked for.
AVAILABILITY_KINDS: dict[Availability, ErrorKind] = {
    "preview_only": ErrorKind.preview_only,
    "drm": ErrorKind.drm,
    "private": ErrorKind.unavailable,
    "geo_blocked": ErrorKind.geo_blocked,
}

# Rules 3 and 4: any AAC stream before legacy MP3, then the higher bitrate, then a single
# progressive file over joined HLS segments.
STREAM_CODEC_ORDER = ("aac", "mp3")
STREAM_PROTOCOL_ORDER = ("http", "hls")
# Rule 1: originals that are kept as they are. ALAC hides in .m4a, so it's told apart by codec.
LOSSLESS_EXTENSIONS = frozenset({"wav", "flac", "aiff", "aif"})
LOSSLESS_CODECS = frozenset({"alac", "flac"})


def choose_format(source: SourceInfo, *, signed_in: bool) -> FormatChoice:
    """The format to download, by rules 1 to 4 of TECH_SPEC §6. Never a preview or hls-aes."""
    if source.availability != "ok":
        kind = AVAILABILITY_KINDS[source.availability]
        raise ScmpError(kind, f"the track's availability is {source.availability}")
    stream = _best_stream(source.formats)
    # Only a signed-in account can download the artist's original file.
    original = original_format(source.formats) if signed_in else None
    if original is not None:
        # Rule 1 keeps a lossless original. Rule 2 keeps a lossy one only if its bitrate holds up.
        return FormatChoice(
            original, needs_bitrate_check=not _is_lossless(original), fallback=stream
        )
    if stream is None:
        raise ScmpError(ErrorKind.engine_outdated, "no AAC or MP3 stream that can be saved")
    return FormatChoice(stream)


def original_format(formats: Sequence[FormatOption]) -> FormatOption | None:
    """The artist's own file among the formats, which yt-dlp calls download."""
    return next((fmt for fmt in formats if fmt.is_original), None)


def is_preview(fmt: FormatOption) -> bool:
    """Whether a format is a 30-second preview."""
    # The format_id check also covers options built without the is_preview flag.
    return fmt.is_preview or "_preview" in fmt.format_id


def _best_stream(formats: Sequence[FormatOption]) -> FormatOption | None:
    """The stream rules 3 and 4 prefer, or None when there's no stream that can be saved."""
    candidates = [fmt for fmt in formats if _is_usable_stream(fmt)]
    return min(candidates, key=_stream_rank, default=None)


def _is_usable_stream(fmt: FormatOption) -> bool:
    """A full AAC or MP3 stream over plain HLS or HTTP, so never a preview or hls-aes."""
    if fmt.is_original or is_preview(fmt):
        return False
    return _protocol(fmt) in STREAM_PROTOCOL_ORDER and fmt.codec in STREAM_CODEC_ORDER


def _stream_rank(fmt: FormatOption) -> tuple[int, float, int]:
    """Lower is better: codec first, then bitrate, then protocol."""
    return (
        STREAM_CODEC_ORDER.index(fmt.codec or ""),
        -(fmt.bitrate or 0.0),
        STREAM_PROTOCOL_ORDER.index(_protocol(fmt)),
    )


def _is_lossless(fmt: FormatOption) -> bool:
    return fmt.ext.lower() in LOSSLESS_EXTENSIONS or fmt.codec in LOSSLESS_CODECS


def _protocol(fmt: FormatOption) -> str:
    """The first part of yt-dlp's format_id: http, hls or hls-aes, or download for the original."""
    return fmt.format_id.split("_", 1)[0]
