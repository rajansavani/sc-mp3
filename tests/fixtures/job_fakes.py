"""Fakes for testing the job manager: the pipeline's slow steps, covers, pools, timers and events.

The pools run nothing until a test says so, which makes every step's order the test's choice.
"""

import json
import shutil
import threading
import time
import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from scmp3.app.events import Event, EventBus
from scmp3.app.job_covers import CoverView, KeptImage
from scmp3.app.jobs import JobManager
from scmp3.app.workers import Task
from scmp3.core import download, paths, pipeline
from scmp3.core.convert import AudioInfo
from scmp3.core.errors import Cancelled, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput
from scmp3.core.library import AskFn, resolve_collision
from scmp3.core.models import (
    ArtworkChoice,
    FormatOption,
    HistoryEntry,
    Progress,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.pipeline import Prepared, Saved, Staged, Tools
from scmp3.core.settings import SettingsStore
from scmp3.core.suggest import suggest

TRACK = "https://soundcloud.com/kaia/midnight-drive"
OTHER = "https://soundcloud.com/kaia/night-bus"
TITLE = "Midnight Drive (feat. Lou V) [prod. Hazy]"
OTHER_TITLE = "Night Bus"
ARTWORK = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-t500x500.jpg"
AAC_STREAM = FormatOption(format_id="hls_aac_160k", ext="m4a", codec="aac", bitrate=160.0)
PREVIEW = "data:image/jpeg;base64,AAAA"

# The order the manual pools run in, so each run goes through a job's steps in a fixed order.
POOL_ORDER = ("resolve", "misc", "download", "save")


def source_for(url: str, title: str = TITLE) -> SourceInfo:
    """A SoundCloud track at `url`, with an AAC stream and artwork."""
    return SourceInfo(
        kind="soundcloud",
        id=f"soundcloud:tracks:{zlib.crc32(url.encode())}",
        url=url,
        title=title,
        uploader="Kaia",
        duration=180.0,
        artwork_url=ARTWORK,
        formats=[AAC_STREAM],
    )


def no_ydl(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the fake pipeline never uses yt-dlp")


@dataclass
class FinalizeCall:
    tags: Tags
    cover: ArtworkChoice
    settings: Settings
    style: TitleStyle | None
    suggestion: TagSuggestion
    overrides: dict[str, str]


@dataclass
class FakeSteps:
    """The pipeline's slow steps. Failures wait in lists, each raised by the next call."""

    sources: dict[str, SourceInfo] = field(default_factory=dict)
    duplicates: dict[str, HistoryEntry] = field(default_factory=dict)
    prepare_errors: list[Exception] = field(default_factory=list)
    fetch_errors: list[Exception] = field(default_factory=list)
    finalize_errors: list[Exception] = field(default_factory=list)
    # Why the next saves' covers couldn't be loaded, as finalize reports it.
    cover_errors: list[ScmpError] = field(default_factory=list)
    # Called in the middle of a step, as a user acting while it runs would.
    during_prepare: Callable[[], None] | None = None
    during_fetch: Callable[[], None] | None = None
    during_finalize: Callable[[], None] | None = None
    # Seconds each step takes, for tests of how calls behave while the pipeline is slow.
    delay: float = 0.0
    # How far each download reports it has got, in order.
    progress: tuple[float, ...] = (50.0,)
    # Fields to set on every suggestion, such as a track total.
    suggestion_extras: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    # The settings each download's format came from, as they were when it ran.
    fetched_settings: list[Settings] = field(default_factory=list)
    finalized: list[FinalizeCall] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _called(self, step: str, job_id: str) -> None:
        with self._lock:
            self.calls.append((step, job_id))
        time.sleep(self.delay)

    def _next_error(self, errors: list[Exception]) -> Exception | None:
        with self._lock:
            return errors.pop(0) if errors else None

    def steps_for(self, job_id: str) -> list[str]:
        return [step for step, called_for in self.calls if called_for == job_id]

    def prepare(self, parsed: ParsedInput, *, settings: Settings, job_id: str) -> Prepared:
        self._called("prepare", job_id)
        if self.during_prepare is not None:
            self.during_prepare()
        error = self._next_error(self.prepare_errors)
        if error:
            raise error
        assert parsed.url is not None
        source = self.sources.get(parsed.url) or source_for(parsed.url)
        return Prepared(
            job_id=job_id,
            source=source,
            suggestion=replace(suggest(source, settings), **self.suggestion_extras),
            duplicate=self.duplicates.get(source.url),
            settings=settings,
            info={},
            tools=Tools(make_ydl=no_ydl, history=History(paths.data_dir() / "history.db")),
        )

    def fetch(
        self,
        prepared: Prepared,
        *,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> Staged:
        self._called("fetch", prepared.job_id)
        self.fetched_settings.append(prepared.settings)
        for pct in self.progress:
            on_progress(Progress(pct=pct, speed=1000.0, eta=3.0))
        if self.during_fetch is not None:
            self.during_fetch()
        if cancel.is_set():
            raise Cancelled
        error = self._next_error(self.fetch_errors)
        if error:
            raise error
        staging = download.new_staging(prepared.job_id)
        path = staging / "audio.mp3"
        path.write_bytes(b"converted audio")
        audio = AudioInfo(duration=180.0, bitrate=320, codec="mp3", sample_rate=44100)
        return Staged(path=path, fmt="mp3", audio=audio, downloaded=AAC_STREAM)

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
        self._called("finalize", prepared.job_id)
        if self.during_finalize is not None:
            self.during_finalize()
        self.finalized.append(FinalizeCall(tags, cover, settings, style, suggestion, overrides))
        error = self._next_error(self.finalize_errors)
        if error:
            raise error
        planned = pipeline.destination(
            prepared.source, suggestion, staged, tags, settings, style=style, overrides=overrides
        )
        dest = resolve_collision(planned, settings.when_file_exists, ask)
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged.path, dest)
        pipeline.discard(prepared)
        if dest is None:
            return None
        with self._lock:
            cover_error = self.cover_errors.pop(0) if self.cover_errors else None
        return Saved(dest, cover_error)

    def discard(self, prepared: Prepared) -> None:
        self._called("discard", prepared.job_id)
        pipeline.discard(prepared)


class FakeCovers:
    """Cover checks that answer at once, unless `source_delay` slows the source's cover.

    A link with "broken" in it answers 404, one with "offline" in it gets no answer, and one
    with "bug" in it fails the way a bug in sc-mp3 would. A file with "damaged" in its name
    isn't an image.
    """

    def __init__(self, source_delay: float = 0.0) -> None:
        self.source_delay = source_delay
        self.source_works = True
        self.source_reads = 0
        # Called while the source's cover is read, as a user acting meanwhile would.
        self.during_source: Callable[[], None] | None = None

    def source(self, source: SourceInfo) -> tuple[CoverView, KeptImage | None]:
        self.source_reads += 1
        time.sleep(self.source_delay)
        if self.during_source is not None:
            self.during_source()
        if not self.source_works:
            return CoverView(url=source.artwork_url), None
        view = CoverView(url=source.artwork_url, width=500, height=500)
        return view, KeptImage(b"source cover", "jpg")

    def link(self, url: str) -> CoverView:
        if "broken" in url:
            raise ScmpError("unavailable", "the link answered 404")
        if "offline" in url:
            raise ScmpError("offline", "no answer")
        if "bug" in url:
            raise RuntimeError("a bug in the cover check")
        return CoverView(url=url, width=1200, height=800)

    def file(self, path: str) -> CoverView:
        if "damaged" in path:
            raise ScmpError("convert_failed", "couldn't read the image")
        return CoverView(url=PREVIEW, width=640, height=480)

    def pasted(self, data_url: str, folder: Path) -> tuple[Path, CoverView]:
        path = folder / "cover-pasted.png"
        path.write_bytes(b"pasted image")
        return path, CoverView(url=PREVIEW, width=300, height=300)


class ManualPool:
    """A pool whose tasks wait until the test runs them."""

    def __init__(self, name: str, size: int) -> None:
        self.name = name
        self.size = size
        self.tasks: deque[Task] = deque()
        self.closed = False

    def submit(self, task: Task) -> None:
        if not self.closed:
            self.tasks.append(task)

    def resize(self, size: int) -> None:
        self.size = size

    def close(self) -> None:
        self.closed = True
        self.tasks.clear()

    def wait_until_idle(self, timeout: float) -> bool:
        return not self.tasks

    def run_one(self) -> None:
        self.tasks.popleft()()


class ManualPools:
    """Makes the manager's pools, and runs their tasks when the test asks."""

    def __init__(self) -> None:
        self.by_name: dict[str, ManualPool] = {}

    def __call__(self, name: str, size: int) -> ManualPool:
        pool = ManualPool(name, size)
        self.by_name[name] = pool
        return pool

    def __getitem__(self, name: str) -> ManualPool:
        return self.by_name[name]

    def run(self, name: str) -> None:
        """Run the tasks waiting in one pool now, but not the ones they queue there."""
        pool = self.by_name[name]
        for _ in range(len(pool.tasks)):
            pool.run_one()

    def run_all(self) -> None:
        """Run tasks until every pool is empty."""
        while any(self.by_name[name].tasks for name in POOL_ORDER):
            for name in POOL_ORDER:
                self.run(name)


@dataclass
class FakeTimer:
    delay: float
    callback: Callable[[], None]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimers:
    """Timers that fire when the test says."""

    def __init__(self) -> None:
        self.started: list[FakeTimer] = []

    def call_later(self, delay: float, callback: Callable[[], None]) -> FakeTimer:
        timer = FakeTimer(delay, callback)
        self.started.append(timer)
        return timer

    def waiting(self) -> list[FakeTimer]:
        return [timer for timer in self.started if not timer.cancelled]

    def fire_all(self) -> None:
        """Fire every timer, as a real one would even after a cancel that came too late."""
        for timer in list(self.started):
            timer.callback()


class RecordingBus(EventBus):
    """An event bus that keeps every event, through JSON as the window gets it."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []
        self._events_lock = threading.Lock()

    def emit(self, event: Event) -> None:
        with self._events_lock:
            self.events.append(json.loads(json.dumps(event)))

    def for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event_job_id(event) == job_id]

    def job_states(self, job_id: str) -> list[str]:
        return [event["job"]["state"] for event in self.for_job(job_id) if event["type"] == "job"]

    def last_job(self, job_id: str) -> dict[str, Any]:
        return [event["job"] for event in self.for_job(job_id) if event["type"] == "job"][-1]


def event_job_id(event: dict[str, Any]) -> str | None:
    if event["type"] == "job":
        return event["job"]["id"]
    return event.get("job_id")


@dataclass
class Harness:
    """A job manager on fakes, with what the tests look at."""

    manager: JobManager
    store: SettingsStore
    steps: FakeSteps
    covers: FakeCovers
    pools: ManualPools
    timers: FakeTimers
    events: RecordingBus
    library: Path

    def add(self, url: str = TRACK) -> str:
        answer = self.manager.add_inputs(url)
        assert answer["rejected"] == [], answer
        (job_id,) = answer["added"]
        return job_id

    def add_and_resolve(self, url: str = TRACK) -> str:
        job_id = self.add(url)
        self.pools.run("resolve")
        return job_id

    def job(self, job_id: str) -> dict[str, Any]:
        """The job as the snapshot shows it now."""
        (job,) = [job for job in self.manager.views() if job["id"] == job_id]
        return job

    def change_settings(self, **patch: Any) -> Settings:
        saved = self.store.update(patch)
        self.manager.settings_changed(saved)
        return saved


def make_harness(library: Path, **settings: Any) -> Harness:
    store = SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(library), **settings})
    steps = FakeSteps(sources={OTHER: source_for(OTHER, OTHER_TITLE)})
    pools = ManualPools()
    timers = FakeTimers()
    events = RecordingBus()
    covers = FakeCovers()
    manager = JobManager(store, events, steps=steps, covers=covers, make_pool=pools, timers=timers)
    return Harness(manager, store, steps, covers, pools, timers, events, library)
