"""Tests for YouTube links through the pipeline: resolving on Deno, the streams it downloads, the
cover it crops, and what happens without YouTube support. A fake yt-dlp replays fixtures."""

import importlib.util
import io
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from mutagen.id3 import ID3
from PIL import Image
from yt_dlp.utils import DownloadError, ExtractorError

from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import ArtworkChoice, Settings
from scmp3.core.pipeline import sources as pipeline_sources


def load(name: str) -> ModuleType:
    """A module in tests/fixtures, which pytest's importlib mode can't import by name."""
    module_name = f"scmp3_test_{name}"
    if module_name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return sys.modules[module_name]


fakes = load("youtube_fakes")
FakeYouTube = fakes.FakeYouTube
DENO = fakes.DENO

TOPIC_ID = "y9qO_xgE34Q"
TOPIC_URL = f"https://www.youtube.com/watch?v={TOPIC_ID}"
THUMBNAILS = f"https://i.ytimg.com/vi/{TOPIC_ID}"


def jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "navy").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture(scope="session")
def tiny_webm(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One second of Opus in WebM, as YouTube's 251 stream comes."""
    dest = tmp_path_factory.mktemp("webm") / "silence.webm"
    command = [str(ffmpeg_exe), "-hide_banner", "-loglevel", "error", "-nostdin"]
    command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1"]
    subprocess.run([*command, "-c:a", "libopus", "-b:a", "160k", str(dest)], check=True)
    return dest


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(library_dir=tmp_path / "library", when_file_exists="keep_both")


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "history.db")


@pytest.fixture
def world(tiny_webm: Path, tiny_m4a: Path) -> FakeYouTube:
    """The Topic upload: Opus and AAC streams, and only the 1280 x 720 hq720 thumbnail."""
    audio = {"251": tiny_webm, "140": tiny_m4a, "*": tiny_webm}
    images = {f"{THUMBNAILS}/hq720.jpg": jpeg(1280, 720)}
    return FakeYouTube(fakes.recorded("topic_upload"), audio, images)


def prepare(
    world: FakeYouTube, settings: Settings, history: History, **kwargs: object
) -> pipeline.Prepared:
    tools = world.tools(history, **kwargs)
    return pipeline.prepare(classify(TOPIC_URL), settings=settings, tools=tools)


def test_a_youtube_link_resolves_on_deno_with_youtube_musics_fields(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    assert world.denos == [DENO.path]
    assert world.extracted == [TOPIC_URL]
    assert prepared.source.kind == "youtube"
    assert prepared.source.id == f"youtube:{TOPIC_ID}"
    assert prepared.suggestion.base_title == "WAIT FOR U"
    assert prepared.suggestion.main_artists == ["Future", "Drake", "Tems"]
    assert (prepared.suggestion.album, prepared.suggestion.year) == ("I NEVER LIKED YOU", 2022)


def test_without_youtube_support_nothing_is_asked_of_youtube(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    with pytest.raises(ScmpError) as error:
        prepare(world, settings, history, deno=None)
    assert error.value.kind == ErrorKind.youtube_support_missing
    assert world.denos == []
    assert world.extracted == []


def test_the_opus_stream_is_downloaded_and_encoded_as_mp3_320(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    staged = pipeline.fetch(prepare(world, settings, history))
    assert world.downloaded == ["251"]
    assert staged.downloaded.codec == "opus"
    assert (staged.fmt, staged.audio.codec, staged.audio.bitrate) == ("mp3", "mp3", 320)
    # The download ran on Deno too.
    assert world.denos == [DENO.path, DENO.path]


def test_keeping_aac_downloads_the_aac_stream_and_keeps_it_as_m4a(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    staged = pipeline.fetch(prepare(world, replace(settings, audio_format="m4a"), history))
    assert world.downloaded == ["140"]
    assert (staged.fmt, staged.audio.codec) == ("m4a", "aac")


def test_youtube_support_removed_after_resolving_stops_the_download(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    prepared.tools = world.tools(history, deno=None)
    with pytest.raises(ScmpError) as error:
        pipeline.fetch(prepared)
    assert error.value.kind == ErrorKind.youtube_support_missing
    assert world.downloaded == []


def test_a_saved_track_gets_its_tags_and_a_square_cover_from_the_largest_thumbnail(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    staged = pipeline.fetch(prepared)
    tags = pipeline.render_tags(prepared.source, prepared.suggestion, settings)
    saved = pipeline.finalize(prepared, staged, tags, ArtworkChoice(), settings)

    assert saved is not None and not saved.cover_missing
    assert saved.path == settings.library_dir / "Future" / "Future, Drake, Tems - WAIT FOR U.mp3"
    # maxresdefault isn't there, so the cover is the next size down, cropped to its middle.
    assert world.image_requests == [f"{THUMBNAILS}/maxresdefault.jpg", f"{THUMBNAILS}/hq720.jpg"]
    written = ID3(saved.path)
    assert str(written["WOAS"]) == TOPIC_URL
    assert str(written["TXXX:SCMP3_SOURCE_ID"]) == f"youtube:{TOPIC_ID}"
    cover = Image.open(io.BytesIO(written.getall("APIC")[0].data))
    assert cover.size == (720, 720)


def test_the_dubbed_video_downloads_its_original_audio_track(
    tiny_webm: Path, settings: Settings, history: History
) -> None:
    world = FakeYouTube(fakes.recorded("dubbed_audio"), {"*": tiny_webm})
    pipeline.fetch(prepare(world, settings, history))
    assert world.downloaded == ["251-22"]


@pytest.mark.parametrize(
    ("message", "kind", "reason"),
    [
        (
            "Private video. Sign in if you've been granted access to this video",
            ErrorKind.unavailable,
            "private",
        ),
        ("Sign in to confirm you\u2019re not a bot", ErrorKind.bot_check, None),
    ],
)
def test_youtubes_refusal_comes_out_as_its_kind_and_reason(
    message: str,
    kind: ErrorKind,
    reason: str | None,
    world: FakeYouTube,
    settings: Settings,
    history: History,
) -> None:
    inner = ExtractorError(f"[youtube] {TOPIC_ID}: {message}", expected=True)
    world.extract_error = DownloadError(f"ERROR: {inner}", exc_info=(type(inner), inner, None))
    with pytest.raises(ScmpError) as error:
        prepare(world, settings, history)
    assert (error.value.kind, error.value.reason) == (kind, reason)


def test_a_live_stream_is_refused_until_it_has_ended(
    world: FakeYouTube, settings: Settings, history: History
) -> None:
    world.info["live_status"] = "is_live"
    with pytest.raises(ScmpError) as error:
        prepare(world, settings, history)
    assert (error.value.kind, error.value.reason) == (ErrorKind.unavailable, "live_stream")


def test_every_kind_of_link_the_pipeline_resolves_has_its_source() -> None:
    assert set(pipeline.RESOLVERS) <= set(pipeline_sources.LINK_SOURCES)
    assert set(pipeline_sources.LINK_SOURCES.values()) <= set(pipeline_sources.SOURCES)
