"""A fake yt-dlp for YouTube that replays the recorded fixtures in tests/fixtures/youtube.

It "downloads" by copying a sample audio file, and serves thumbnails from a table. Test files load
this module with importlib, since pytest's importlib mode can't import it by name.
"""

import copy
import importlib.util
import io
import shutil
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError

from scmp3.core import pipeline
from scmp3.core.history import History
from scmp3.core.sources.deno import Deno

DENO = Deno(Path("deno-for-tests.exe"), "2.9.8", managed=True)


def load_recorder() -> ModuleType:
    """tests/fixtures/record_youtube.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_record_youtube"
    if name not in sys.modules:
        path = Path(__file__).parent / "record_youtube.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


recorder = load_recorder()


def recorded(name: str, seconds: float = 1.0) -> dict[str, Any]:
    """A fixture's info dict, made as long as the sample audio and extracted just now."""
    info = recorder.load(name)
    info.update(duration=seconds, epoch=int(time.time()))
    return info


class ImageResponse(io.BytesIO):
    """A thumbnail as an opener's response."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Type": "image/jpeg"}


def not_found(url: str) -> HTTPError:
    return HTTPError(Response(io.BytesIO(b""), url, {}, status=404))


class FakeYoutubeYdl:
    """Stands in for a YoutubeDL made for YouTube, answering from one info dict."""

    def __init__(self, world: "FakeYouTube", hook: Any, extra: Mapping[str, Any]) -> None:
        self.world = world
        self.hook = hook
        self.params = dict(extra)

    def __enter__(self) -> "FakeYoutubeYdl":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self.world.extracted.append(url)
        if self.world.extract_error is not None:
            raise self.world.extract_error
        return copy.deepcopy(self.world.info)

    def process_ie_result(self, info: dict[str, Any], download: bool = True) -> dict[str, Any]:
        format_id = self.params["format"]
        self.world.downloaded.append(format_id)
        sample = self.world.audio.get(format_id) or self.world.audio["*"]
        template = self.params["outtmpl"].replace("%(ext)s", sample.suffix[1:])
        path = Path(template.replace("%%", "%"))
        self.hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        shutil.copyfile(sample, path)
        self.hook({"status": "finished", "downloaded_bytes": 2, "total_bytes": 2})
        return {**info, "requested_downloads": [{"filepath": str(path)}]}

    def urlopen(self, url: str) -> ImageResponse:
        self.world.image_requests.append(url)
        if url not in self.world.images:
            raise not_found(url)
        return ImageResponse(self.world.images[url])


@dataclass
class FakeYouTube:
    """Makes FakeYoutubeYdl instances that all answer from one recorded video.

    Call it as Tools.make_youtube_ydl. `plain` stands in for Tools.make_ydl, which saving uses
    to fetch the cover.
    """

    info: dict[str, Any]
    audio: dict[str, Path]  # the file each format "downloads" as, by format_id, or "*" for any
    images: dict[str, bytes] = field(default_factory=dict)
    extract_error: Exception | None = None
    denos: list[Path] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    image_requests: list[str] = field(default_factory=list)

    def __call__(
        self,
        deno: Path,
        *,
        progress_hook: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> FakeYoutubeYdl:
        self.denos.append(deno)
        return FakeYoutubeYdl(self, progress_hook, extra or {})

    def plain(
        self,
        token: str | None = None,
        *,
        progress_hook: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> FakeYoutubeYdl:
        return FakeYoutubeYdl(self, progress_hook, extra or {})

    def tools(self, history: History, deno: Deno | None = DENO) -> pipeline.Tools:
        """Pipeline tools that reach this fake, with `deno` as YouTube support."""
        return pipeline.Tools(
            make_ydl=self.plain,
            history=history,
            make_youtube_ydl=self,
            find_deno=lambda: deno,
        )
