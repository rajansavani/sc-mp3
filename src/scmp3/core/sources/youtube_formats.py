"""Choose the YouTube audio stream to download (TECH_SPEC §7)."""

from collections.abc import Sequence

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import AudioFormat, FormatChoice, FormatOption, SourceInfo

# YouTube's audio streams by itag: 251 is Opus at about 160 kbps, 140 is AAC at 128 kbps.
OPUS_160 = "251"
AAC_128 = "140"
# What each audio format setting takes, best first. MP3 is encoded from the Opus stream, and
# "keep AAC as .m4a" copies the AAC one. The smaller streams are never used, so a video missing
# both says so rather than saving thin audio.
ITAG_ORDER: dict[AudioFormat, tuple[str, ...]] = {
    "mp3_320": (OPUS_160, AAC_128),
    "mp3_v0": (OPUS_160, AAC_128),
    "m4a": (AAC_128, OPUS_160),
}


def choose_format(source: SourceInfo, *, audio_format: AudioFormat) -> FormatChoice:
    """The stream to download for the audio format setting. Raises when neither stream is there."""
    by_itag = _best_by_itag(source.formats)
    for itag in ITAG_ORDER[audio_format]:
        if itag in by_itag:
            return FormatChoice(by_itag[itag])
    raise ScmpError(ErrorKind.engine_outdated, "YouTube listed neither the 251 nor the 140 stream")


def itag(fmt: FormatOption) -> str:
    """The itag a format_id starts with, as in 251 for "251-1", a second audio track."""
    return fmt.format_id.split("-", 1)[0]


def _best_by_itag(formats: Sequence[FormatOption]) -> dict[str, FormatOption]:
    """For each itag, the format with the highest bitrate."""
    best: dict[str, FormatOption] = {}
    for fmt in formats:
        kept = best.get(itag(fmt))
        if kept is None or (fmt.bitrate or 0) > (kept.bitrate or 0):
            best[itag(fmt)] = fmt
    return best
