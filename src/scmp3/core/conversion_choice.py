"""Picking how a download becomes the saved file, and whether a lossy original is worth keeping.

`convert` runs the conversions. This picks one from the download's codec and the audio format
setting (PLAN D4, TECH_SPEC §6 and §9).
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scmp3.core import convert
from scmp3.core.errors import ScmpError
from scmp3.core.models import AudioFormat, FormatOption
from scmp3.core.tagger import FileFormat

log = logging.getLogger(__name__)


class Converter(Protocol):
    """One of convert's functions: makes `dst` from `src`, stopping when `cancel` is set."""

    def __call__(self, src: Path, dst: Path, *, cancel: threading.Event | None = None) -> Path: ...


@dataclass(frozen=True)
class Conversion:
    """One way to turn a download into the file that gets saved."""

    fmt: FileFormat  # the saved file's format, which is also its extension
    run: Converter


def _copy_mp3(src: Path, dst: Path, *, cancel: threading.Event | None = None) -> Path:
    # A copy takes moments, so the caller checks for a cancel once it's done.
    return convert.copy_mp3(src, dst)


COPY_MP3 = Conversion("mp3", _copy_mp3)
REMUX_M4A = Conversion("m4a", convert.remux_m4a)
MP3_320 = Conversion("mp3", convert.to_mp3_320)
# What each audio format setting encodes to, for downloads that can't be kept as they are.
ENCODERS: dict[AudioFormat, Conversion] = {
    "mp3_320": MP3_320,
    "mp3_v0": Conversion("mp3", convert.to_mp3_v0),
    # Only AAC can stay in an M4A, so anything else becomes MP3 320.
    "m4a": MP3_320,
}


def choose_conversion(audio_format: AudioFormat, codec: str | None) -> Conversion:
    """How a download in `codec` becomes the saved file under the audio format setting."""
    # MP3 is copied, since encoding it again would lose quality.
    if codec == "mp3":
        return COPY_MP3
    if codec == "aac" and audio_format == "m4a":
        return REMUX_M4A
    return ENCODERS[audio_format]


def original_holds_up(path: Path, stream: FormatOption) -> bool:
    """Whether a downloaded lossy original's bitrate is at least the stream's (TECH_SPEC §6)."""
    try:
        bitrate = convert.probe(path).bitrate
    except ScmpError as error:
        log.warning("Couldn't read the original's bitrate, so the stream is used: %s", error)
        return False
    if bitrate is None:
        log.info("The original doesn't say its bitrate, so the stream is used")
        return False
    if bitrate < (stream.bitrate or 0):
        log.info("The original is %d kbps, below the stream, so the stream is used", bitrate)
        return False
    return True
