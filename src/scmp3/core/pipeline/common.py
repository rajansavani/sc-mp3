"""What the pipeline's stages share: their collaborators, the data passed between them, and the
error mapping that makes every failure a ScmpError."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from scmp3.core import download
from scmp3.core.convert import AudioInfo
from scmp3.core.engine import ProgressHook
from scmp3.core.errors import Cancelled, ErrorKind, ErrorPlace, ScmpError
from scmp3.core.history import History
from scmp3.core.history import default_path as default_history_path
from scmp3.core.models import FormatOption, HistoryEntry, Settings, SourceInfo, TagSuggestion
from scmp3.core.sources import soundcloud, ytdlp_errors
from scmp3.core.tagger import FileFormat
from scmp3.core.urls import UnsupportedInput

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL


class YdlMaker(Protocol):
    """Builds a YoutubeDL for a SoundCloud token, like `soundcloud.make_ydl`."""

    def __call__(
        self,
        token: str | None = None,
        *,
        progress_hook: ProgressHook | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> YoutubeDL: ...


def _default_history() -> History:
    return History(default_history_path())


@dataclass(frozen=True)
class Tools:
    """How the pipeline reaches yt-dlp and the history. Tests pass fakes."""

    make_ydl: YdlMaker = soundcloud.make_ydl
    history: History = field(default_factory=_default_history)


@dataclass
class Prepared:
    """A resolved track with its suggested tags, ready to download and review."""

    job_id: str  # also the staging folder's name
    source: SourceInfo
    suggestion: TagSuggestion
    duplicate: HistoryEntry | None  # an earlier save of the same track
    settings: Settings  # what prepare ran with; fetch converts to its audio format
    info: dict[str, Any] = field(repr=False)  # yt-dlp's info dict, which downloads start from
    token: str | None = field(default=None, repr=False)  # the SoundCloud sign-in, if any
    tools: Tools = field(default_factory=Tools, repr=False)


@dataclass(frozen=True)
class Staged:
    """The converted audio in the staging folder, untagged until finalize."""

    path: Path  # <staging>/audio.mp3 or audio.m4a
    fmt: FileFormat
    audio: AudioInfo  # what the converted file holds
    downloaded: FormatOption  # the stream or original it came from


@dataclass(frozen=True)
class Saved:
    """A file finalize put in the library."""

    path: Path
    # Why the cover couldn't be downloaded or read, when the file was saved without it.
    cover_error: ScmpError | None = None

    @property
    def cover_missing(self) -> bool:
        return self.cover_error is not None


def expected_duration(source: SourceInfo) -> float | None:
    """The track's real length. yt-dlp reports a preview's 30 s, so the full length comes first."""
    return source.full_duration or source.duration


def discard(prepared: Prepared) -> None:
    """Delete the job's staging folder, as after a skip or a failure."""
    download.cleanup(prepared.job_id)


@contextmanager
def only_scmp_errors(*, where: ErrorPlace) -> Iterator[None]:
    """Let ScmpError, Cancelled and UnsupportedInput through, and turn anything else into ScmpError.

    yt-dlp's errors get their kind from ytdlp_errors, and anything unexpected is internal. A disk
    error that doesn't say which folder it was about is marked as being about `where`.
    """
    try:
        yield
    except (Cancelled, UnsupportedInput):
        raise
    except ScmpError as error:
        _mark_place(error, where)
        raise
    except Exception as error:
        mapped = ytdlp_errors.to_scmp_error(error)
        _mark_place(mapped, where)
        raise mapped from error


def _mark_place(error: ScmpError, where: ErrorPlace) -> None:
    """Say which folder a disk error was about, unless the step that raised it already did."""
    if error.kind == ErrorKind.disk and error.where is None:
        error.where = where
