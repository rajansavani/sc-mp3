"""Fakes and fixtures the pipeline tests share.

A fake yt-dlp replays T-013's recorded fixtures, "downloads" sample audio and serves covers, and
the helpers run the pipeline's stages with it. Test files load this module with `load_fakes`.
"""

import importlib.util
import io
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from scmp3.core import paths, pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput, classify
from scmp3.core.models import ArtworkChoice, HistoryEntry, Settings


def load_record_module() -> ModuleType:
    """tests/fixtures/record.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_record"
    if name not in sys.modules:
        path = Path(__file__).parent / "record.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


record = load_record_module()

TYPICAL_URL = "https://soundcloud.com/the80m/the-following"
TYPICAL_ID = "soundcloud:tracks:343609555"
ARTWORK = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d"
SHORT_URL = "https://on.soundcloud.com/AbCdEfGh"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"


def png(side: int = 600) -> bytes:
    """A square PNG cover."""
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), "teal").save(buffer, "PNG")
    return buffer.getvalue()


class ImageResponse(io.BytesIO):
    """A cover image as an opener's response."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Type": "image/png"}


@dataclass
class Redirected:
    """The response to a short link: only the final URL matters."""

    url: str

    def close(self) -> None:
        pass


class FakeYdl(record.ReplayYdl):
    """A ReplayYdl that also downloads, by copying a sample, and serves covers and redirects."""

    def __init__(self, world: "FakeSoundCloud", hook: Any, extra: Mapping[str, Any]) -> None:
        super().__init__(world.fixture)
        self.world = world
        self.hook = hook
        self.params = dict(extra)

    def __enter__(self) -> "FakeYdl":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self.world.extracted.append(url)
        return super().extract_info(url, download)

    def process_ie_result(self, info: dict[str, Any], download: bool = True) -> dict[str, Any]:
        format_id = self.params["format"]
        self.world.downloaded.append(format_id)
        if self.world.download_error is not None:
            raise self.world.download_error
        sample = self.world.audio.get(format_id) or self.world.audio["*"]
        template = self.params["outtmpl"].replace("%(ext)s", sample.suffix[1:])
        path = Path(template.replace("%%", "%"))
        self.hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        shutil.copyfile(sample, path)
        self.hook({"status": "finished", "downloaded_bytes": 2, "total_bytes": 2})
        return {**info, "requested_downloads": [{"filepath": str(path)}]}

    def urlopen(self, request: Any) -> Any:
        if not isinstance(request, str):
            return super().urlopen(request)
        if request in self.world.redirects:
            return Redirected(self.world.redirects[request])
        self.world.image_requests.append(request)
        if request not in self.world.images:
            raise record.http_error(404, request)
        return ImageResponse(self.world.images[request])


@dataclass
class FakeSoundCloud:
    """A make_ydl that hands out FakeYdl instances, all answering from one fixture."""

    fixture: dict[str, Any]
    audio: dict[str, Path]  # the file each format "downloads" as, by format_id, or "*" for any
    images: dict[str, bytes] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)
    download_error: Exception | None = None
    tokens: list[str | None] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    image_requests: list[str] = field(default_factory=list)

    def __call__(
        self,
        token: str | None = None,
        *,
        progress_hook: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> FakeYdl:
        self.tokens.append(token)
        return FakeYdl(self, progress_hook, extra or {})


class BrokenHistory(History):
    """A history whose database can't be used."""

    def find_by_source_id(self, source_id: str) -> HistoryEntry | None:
        raise ScmpError(ErrorKind.disk, "history database error: disk I/O error")

    def add(self, entry: HistoryEntry) -> HistoryEntry:
        raise ScmpError(ErrorKind.disk, "history database error: disk I/O error")


def recorded(name: str, seconds: float = 1.0) -> dict[str, Any]:
    """A fixture made as long as the sample audio, and extracted just now."""
    data = record.load(name)
    if data["info"] is not None:
        data["info"].update(duration=seconds, epoch=int(time.time()))
    if data["api"] is not None:
        data["api"]["full_duration"] = int(seconds * 1000)
    return data


def silence(ffmpeg: Path, dest: Path, seconds: int, codec: list[str]) -> Path:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin"]
    command += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds)]
    subprocess.run([*command, *codec, str(dest)], check=True)
    return dest


@pytest.fixture(scope="session")
def preview_m4a(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Thirty seconds of AAC, as long as a SoundCloud preview."""
    dest = tmp_path_factory.mktemp("preview") / "preview.m4a"
    return silence(ffmpeg_exe, dest, 30, ["-c:a", "aac", "-b:a", "32k"])


@pytest.fixture(scope="session")
def low_mp3(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One second of MP3 at 64 kbps, below SoundCloud's 160 kbps stream."""
    dest = tmp_path_factory.mktemp("low") / "low.mp3"
    return silence(ffmpeg_exe, dest, 1, ["-c:a", "libmp3lame", "-b:a", "64k"])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(library_dir=tmp_path / "library", when_file_exists="keep_both")


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "history.db")


@pytest.fixture
def world(tiny_m4a: Path) -> FakeSoundCloud:
    """The typical track: AAC streams, and a cover at every size."""
    images = {f"{ARTWORK}-original.jpg": png(), f"{ARTWORK}-t500x500.jpg": png(500)}
    return FakeSoundCloud(recorded("aac_typical"), {"*": tiny_m4a}, images)


def link(url: str = TYPICAL_URL) -> ParsedInput:
    return classify(url)


def prepare(
    world: FakeSoundCloud, settings: Settings, history: History, **kwargs: Any
) -> pipeline.Prepared:
    tools = pipeline.Tools(make_ydl=world, history=history)
    parsed = kwargs.pop("parsed", None) or link(world.fixture["url"])
    return pipeline.prepare(parsed, settings=settings, tools=tools, **kwargs)


def save(prepared: pipeline.Prepared, settings: Settings, **kwargs: Any) -> Path | None:
    """Fetch and finalize with the suggested tags and the source's cover. Returns the path."""
    saved = finalize(prepared, settings, **kwargs)
    return None if saved is None else saved.path


def finalize(
    prepared: pipeline.Prepared, settings: Settings, **kwargs: Any
) -> pipeline.Saved | None:
    """Fetch and finalize with the suggested tags and the source's cover."""
    staged = pipeline.fetch(prepared)
    tags = pipeline.render_tags(prepared.source, prepared.suggestion, settings)
    cover = kwargs.pop("cover", ArtworkChoice())
    return pipeline.finalize(prepared, staged, tags, cover, settings, **kwargs)


def staging_files() -> list[str]:
    root = paths.staging_dir()
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []
