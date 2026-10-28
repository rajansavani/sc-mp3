"""The pipeline's slow steps as the job manager calls them, so tests can pass fakes."""

import threading
from collections.abc import Mapping, Sequence
from typing import Protocol

from scmp3.core import pipeline
from scmp3.core.download import ProgressCallback
from scmp3.core.inputs import ParsedInput
from scmp3.core.library import AskFn
from scmp3.core.models import (
    ArtworkChoice,
    HistoryEntry,
    PlaylistInfo,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.pipeline import (
    AlbumPlace,
    Prepared,
    PreparedSet,
    Saved,
    SetTrack,
    Staged,
    Tools,
    TrackResult,
)
from scmp3.core.sources import soundcloud_sets


class Steps(Protocol):
    """Resolving, downloading, saving, re-tagging, cleaning up and restoring a job, and listing
    and saving a set. Only the restores are quick."""

    def prepare(self, parsed: ParsedInput, *, settings: Settings, job_id: str) -> Prepared: ...

    def restore(
        self,
        job_id: str,
        source: SourceInfo,
        suggestion: TagSuggestion,
        duplicate: HistoryEntry | None,
        *,
        settings: Settings,
    ) -> Prepared: ...

    def duplicate_of(self, source: SourceInfo) -> HistoryEntry | None: ...

    def fetch(
        self, prepared: Prepared, *, on_progress: ProgressCallback, cancel: threading.Event
    ) -> Staged: ...

    def finalize(
        self,
        prepared: Prepared,
        staged: Staged,
        tags: Tags,
        cover: ArtworkChoice,
        settings: Settings,
        *,
        style: TitleStyle | None,
        suggestion: TagSuggestion,
        overrides: dict[str, str],
        ask: AskFn,
    ) -> Saved | None: ...

    def retag(
        self, prepared: Prepared, tags: Tags, cover: ArtworkChoice, settings: Settings
    ) -> Saved: ...

    def discard(self, prepared: Prepared) -> None: ...

    def prepare_set(self, parsed: ParsedInput, *, settings: Settings) -> PreparedSet: ...

    def restore_set(
        self, url: str, playlist: PlaylistInfo, *, settings: Settings
    ) -> PreparedSet: ...

    def album_places(
        self,
        prepared_set: PreparedSet,
        tracks: Sequence[SetTrack],
        settings: Settings,
        *,
        album: Mapping[str, str],
        cover: ArtworkChoice,
    ) -> list[AlbumPlace | None]: ...

    def finalize_set_track(
        self, track: SetTrack, place: AlbumPlace | None, settings: Settings, *, ask: AskFn
    ) -> TrackResult: ...


class PipelineSteps:
    """The steps in core/pipeline.py, with yt-dlp and the history from `tools`."""

    def __init__(self, tools: Tools) -> None:
        self._tools = tools

    def prepare(self, parsed: ParsedInput, *, settings: Settings, job_id: str) -> Prepared:
        # TODO(T-041): Pass auth.current_token() to prepare.
        return pipeline.prepare(parsed, settings=settings, tools=self._tools, job_id=job_id)

    def restore(
        self,
        job_id: str,
        source: SourceInfo,
        suggestion: TagSuggestion,
        duplicate: HistoryEntry | None,
        *,
        settings: Settings,
    ) -> Prepared:
        """What prepare gave a job before a restart, without resolving it again."""
        # yt-dlp's info isn't kept, so a download extracts it again, as for info that's too old.
        # TODO(T-041): Pass auth.current_token() here too.
        return Prepared(
            job_id=job_id,
            source=source,
            suggestion=suggestion,
            duplicate=duplicate,
            settings=settings,
            info={},
            tools=self._tools,
        )

    def duplicate_of(self, source: SourceInfo) -> HistoryEntry | None:
        """The latest save of this source in the history, as prepare looks it up."""
        return pipeline.find_duplicate(self._tools.history, source)

    def fetch(
        self, prepared: Prepared, *, on_progress: ProgressCallback, cancel: threading.Event
    ) -> Staged:
        return pipeline.fetch(prepared, on_progress=on_progress, cancel=cancel)

    def finalize(
        self,
        prepared: Prepared,
        staged: Staged,
        tags: Tags,
        cover: ArtworkChoice,
        settings: Settings,
        *,
        style: TitleStyle | None,
        suggestion: TagSuggestion,
        overrides: dict[str, str],
        ask: AskFn,
    ) -> Saved | None:
        return pipeline.finalize(
            prepared,
            staged,
            tags,
            cover,
            settings,
            style=style,
            suggestion=suggestion,
            overrides=overrides,
            ask=ask,
        )

    def retag(
        self, prepared: Prepared, tags: Tags, cover: ArtworkChoice, settings: Settings
    ) -> Saved:
        return pipeline.retag(prepared, tags, cover, settings)

    def discard(self, prepared: Prepared) -> None:
        pipeline.discard(prepared)

    def prepare_set(self, parsed: ParsedInput, *, settings: Settings) -> PreparedSet:
        # TODO(T-041): Pass auth.current_token() to prepare_set.
        return pipeline.prepare_set(parsed, settings=settings, tools=self._tools)

    def restore_set(self, url: str, playlist: PlaylistInfo, *, settings: Settings) -> PreparedSet:
        """What prepare_set gave a set before a restart, without listing it again."""
        return PreparedSet(
            url=url,
            playlist=playlist,
            default_mode=soundcloud_sets.default_mode(playlist),
            settings=settings,
            tools=self._tools,
        )

    def album_places(
        self,
        prepared_set: PreparedSet,
        tracks: Sequence[SetTrack],
        settings: Settings,
        *,
        album: Mapping[str, str],
        cover: ArtworkChoice,
    ) -> list[AlbumPlace | None]:
        return pipeline.album_places(prepared_set, tracks, settings, album=album, cover=cover)

    def finalize_set_track(
        self, track: SetTrack, place: AlbumPlace | None, settings: Settings, *, ask: AskFn
    ) -> TrackResult:
        return pipeline.finalize_set_track(track, place, settings, ask=ask)
