"""Fakes for testing sets in the job manager: a set's listing, its album's cover and its saves.

`SetSteps` adds the set steps to job_fakes.FakeSteps. Each set is a list of track links that
resolve as job_fakes' tracks do, and a save copies the staged file to where the real one would go.
Test files load this module with `load_set_job_fakes`.
"""

import importlib.util
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from scmp3.core import paths, pipeline
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.library import AskFn, resolve_collision, reuse_folders_by_case
from scmp3.core.models import ArtworkChoice, PlaylistEntry, PlaylistInfo, Settings, Tags
from scmp3.core.pipeline import AlbumPlace, LoadedCover, PreparedSet, Saved, SetTrack, TrackResult
from scmp3.core.settings import SettingsStore
from scmp3.core.sources import soundcloud_sets


def load_job_fakes() -> ModuleType:
    """tests/fixtures/job_fakes.py, under the name the job manager's tests load it by."""
    name = "scmp3_test_fixture_job_fakes"
    if name not in sys.modules:
        path = Path(__file__).parent / "job_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fakes = load_job_fakes()

ALBUM = "https://soundcloud.com/kaia/sets/night-drives"
PLAYLIST = "https://soundcloud.com/kaia/sets/late-mix"
ALBUM_COVER = "https://i1.sndcdn.com/artworks-night-drives-t500x500.jpg"
# The album's cover as the fake loads it, which a saved track's call records.
LOADED_COVER = Cover(
    jpeg=b"album cover", width=500, height=500, source_width=500, source_height=500
)


def track_link(set_url: str, number: int) -> str:
    return f"{set_url.replace('/sets/', '/')}-track-{number}"


def playlist_for(
    url: str,
    count: int,
    *,
    set_type: str | None = "album",
    title: str = "Night Drives",
    cover: str | None = ALBUM_COVER,
) -> PlaylistInfo:
    """A set of `count` tracks by Kaia, listed with their links."""
    entries = [
        PlaylistEntry(url=track_link(url, n), title=f"Track {n}") for n in range(1, count + 1)
    ]
    return PlaylistInfo(
        title=title,
        owner="Kaia",
        set_type=set_type,
        is_album=set_type == "album",
        artwork_url=cover,
        entries=entries,
    )


@dataclass
class SetSaveCall:
    """One track saved by finalize_set_track, with what it was saved with."""

    job_id: str
    tags: Tags
    place: AlbumPlace | None
    overrides: dict[str, str]
    cover: ArtworkChoice
    path: Path | None


@dataclass
class SetSteps(fakes.FakeSteps):
    """job_fakes' steps, with sets."""

    sets: dict[str, PlaylistInfo] = field(default_factory=dict)
    list_errors: list[Exception] = field(default_factory=list)
    # Why a track's save fails, by its job id.
    set_save_errors: dict[str, ScmpError] = field(default_factory=dict)
    # How many more tracks resolve before SoundCloud answers 429, or None for no limit.
    resolve_budget: int | None = None
    # How many album covers were loaded, and with which choice each time.
    cover_loads: list[ArtworkChoice] = field(default_factory=list)
    # Whose own cover each load would use for a "source" choice: the set's or a track's link.
    cover_owners: list[str] = field(default_factory=list)
    cover_works: bool = True
    set_saves: list[SetSaveCall] = field(default_factory=list)

    def prepare(self, parsed: ParsedInput, *, settings: Settings, job_id: str) -> Any:
        with self._lock:
            limited = self.resolve_budget is not None and self.resolve_budget <= 0
            if self.resolve_budget is not None and not limited:
                self.resolve_budget -= 1
        if limited:
            self._called("prepare", job_id)
            raise ScmpError(ErrorKind.rate_limited, "SoundCloud answered 429")
        return super().prepare(parsed, settings=settings, job_id=job_id)

    def prepare_set(self, parsed: ParsedInput, *, settings: Settings) -> PreparedSet:
        assert parsed.url is not None
        self._called("prepare_set", parsed.url)
        error = self._next_error(self.list_errors)
        if error:
            raise error
        return self.restore_set(parsed.url, self.sets[parsed.url], settings=settings)

    def restore_set(self, url: str, playlist: PlaylistInfo, *, settings: Settings) -> PreparedSet:
        return PreparedSet(
            url=url,
            playlist=playlist,
            default_mode=soundcloud_sets.default_mode(playlist),
            settings=settings,
            tools=self.tools(),
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
        first = next((track.prepared.source for track in tracks if track.prepared), None)
        owner = pipeline.album_cover_source(prepared_set.playlist, first) if first else None
        with self._lock:
            self.cover_loads.append(cover)
            self.cover_owners.append(getattr(owner, "url", prepared_set.url))
        loaded = LoadedCover(LOADED_COVER if self.cover_works else None)
        if not self.cover_works:
            loaded = LoadedCover(None, ScmpError(ErrorKind.offline, "no answer"))
        suggestions = [t.suggestion or t.prepared.suggestion for t in tracks if t.to_save]
        fields = {**pipeline.album_fields(prepared_set.playlist, suggestions), **album}
        numbers = pipeline.track_numbers([track.to_save for track in tracks])
        return [
            None if number is None else AlbumPlace(fields, number, len(suggestions), loaded)
            for number in numbers
        ]

    def finalize_set_track(
        self, track: SetTrack, place: AlbumPlace | None, settings: Settings, *, ask: AskFn
    ) -> TrackResult:
        prepared = track.prepared
        assert prepared is not None and track.staged is not None
        self._called("finalize_set_track", prepared.job_id)
        if self.during_finalize is not None:
            self.during_finalize()
        with self._lock:
            error = self.set_save_errors.pop(prepared.job_id, None)
        if error:
            return _result("failed", place, error=error)
        overrides = track.overrides if place is None else {**place.fields, **track.overrides}
        tags = self._tags(track, place, overrides, settings)
        planned = pipeline.destination(
            prepared.source,
            track.suggestion or prepared.suggestion,
            track.staged,
            tags,
            settings,
            style=track.style,
            overrides=overrides,
            in_album_set=place is not None,
        )
        planned = reuse_folders_by_case(planned, settings.library_dir)
        dest = resolve_collision(planned, settings.when_file_exists, ask)
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(track.staged.path, dest)
        pipeline.discard(prepared)
        with self._lock:
            call = SetSaveCall(prepared.job_id, tags, place, overrides, track.cover, dest)
            self.set_saves.append(call)
        if dest is None:
            return _result("name_taken", place)
        cover_error = place.cover.error if place is not None else None
        return _result("saved", place, saved=Saved(dest, cover_error))

    def _tags(
        self,
        track: SetTrack,
        place: AlbumPlace | None,
        overrides: Mapping[str, str],
        settings: Settings,
    ) -> Tags:
        prepared = track.prepared
        assert prepared is not None
        suggestion = track.suggestion or prepared.suggestion
        if place is None:
            return pipeline.render_tags(
                prepared.source, suggestion, settings, style=track.style, overrides=overrides
            )
        return pipeline.render_album_track(
            prepared.source,
            suggestion,
            settings,
            track_no=place.track_no,
            track_total=place.track_total,
            style=track.style,
            overrides=overrides,
        )

    def saves_for(self, job_id: str) -> list[SetSaveCall]:
        return [call for call in self.set_saves if call.job_id == job_id]


def _result(
    outcome: pipeline.TrackOutcome,
    place: AlbumPlace | None,
    *,
    saved: Saved | None = None,
    error: ScmpError | None = None,
) -> TrackResult:
    numbers = (place.track_no, place.track_total) if place else (None, None)
    return TrackResult(outcome, saved, error, *numbers)


def make_set_harness(library: Path, *, queue: Any = None, **settings: Any) -> Any:
    """job_fakes' harness on SetSteps, which knows the album and the playlist sets."""
    store = SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(library), **settings})
    return harness_on(store, library, queue)


def add_set(steps: SetSteps, url: str, playlist: PlaylistInfo) -> None:
    """Make a set known to the fake, with a source for each of its tracks."""
    steps.sets[url] = playlist
    for entry in playlist.entries:
        steps.sources[entry.url] = fakes.source_for(entry.url, entry.title or "")


def harness_on(store: SettingsStore, library: Path, queue: Any) -> Any:
    steps = SetSteps()
    add_set(steps, ALBUM, playlist_for(ALBUM, 5))
    add_set(steps, PLAYLIST, playlist_for(PLAYLIST, 3, set_type=None, title="Late Mix"))
    pools = fakes.ManualPools()
    timers = fakes.FakeTimers()
    events = fakes.RecordingBus()
    covers = fakes.FakeCovers()
    manager = fakes.JobManager(
        store, events, steps=steps, covers=covers, make_pool=pools, timers=timers, queue=queue
    )
    return fakes.Harness(manager, store, steps, covers, pools, timers, events, library, queue)


def restarted(harness: Any) -> Any:
    """The app closed and opened again, on the same settings, queue.json and sets."""
    harness.manager.shut_down()
    queue = fakes.QueueFile(harness.queue.path)
    again = harness_on(harness.store, harness.library, queue)
    for url, playlist in harness.steps.sets.items():
        add_set(again.steps, url, playlist)
    again.manager.start_up()
    return again
