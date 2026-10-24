"""What the pipeline does differently for each source: the YoutubeDL it needs and how it picks a
format. A new source adds one entry to SOURCES and its link kinds to LINK_SOURCES."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from scmp3.core.engine import ProgressHook
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import FormatChoice, Settings, SourceInfo, SourceKind
from scmp3.core.pipeline.common import Tools, YdlMaker
from scmp3.core.sources import soundcloud_formats, youtube_formats

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

# Picks the format to download, by the source's rules.
FormatChooser = Callable[[SourceInfo, Settings, bool], FormatChoice]


@dataclass(frozen=True)
class SourceKit:
    """How the pipeline handles one source."""

    ydl_maker: Callable[[Tools], YdlMaker]
    choose_format: FormatChooser


def _soundcloud_maker(tools: Tools) -> YdlMaker:
    return tools.make_ydl


def _youtube_maker(tools: Tools) -> YdlMaker:
    """A maker that runs YouTube's scripts on Deno. Without Deno, YouTube support is missing."""
    deno = tools.find_deno()
    if deno is None:
        # Raised before any request, so yt-dlp never runs without a JavaScript runtime.
        raise ScmpError(ErrorKind.youtube_support_missing, "Deno isn't installed")
    make_youtube_ydl = tools.make_youtube_ydl

    def make(
        token: str | None = None,
        *,
        progress_hook: ProgressHook | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> "YoutubeDL":
        # The token is a SoundCloud sign-in, which YouTube has no use for.
        return make_youtube_ydl(deno.path, progress_hook=progress_hook, extra=extra)

    return make


def _soundcloud_format(source: SourceInfo, settings: Settings, signed_in: bool) -> FormatChoice:
    return soundcloud_formats.choose_format(source, signed_in=signed_in)


def _youtube_format(source: SourceInfo, settings: Settings, signed_in: bool) -> FormatChoice:
    return youtube_formats.choose_format(source, audio_format=settings.audio_format)


SOURCES: dict[SourceKind, SourceKit] = {
    "soundcloud": SourceKit(_soundcloud_maker, _soundcloud_format),
    "youtube": SourceKit(_youtube_maker, _youtube_format),
}
# The source each kind of link comes from.
LINK_SOURCES: dict[str, SourceKind] = {
    "soundcloud_track": "soundcloud",
    "soundcloud_short": "soundcloud",
    "soundcloud_set": "soundcloud",
    "youtube_video": "youtube",
}


def ydl_maker(tools: Tools, source: SourceKind) -> YdlMaker:
    """The YoutubeDL maker for a source. YouTube's raises youtube_support_missing without Deno."""
    return SOURCES[source].ydl_maker(tools)


def link_ydl_maker(tools: Tools, link_kind: str) -> YdlMaker:
    """The YoutubeDL maker for the source a kind of link comes from."""
    return ydl_maker(tools, LINK_SOURCES[link_kind])


def choose_format(source: SourceInfo, settings: Settings, *, signed_in: bool) -> FormatChoice:
    """The format to download from a resolved source, by its own rules."""
    return SOURCES[source.kind].choose_format(source, settings, signed_in)
