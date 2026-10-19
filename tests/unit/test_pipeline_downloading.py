"""Tests for `fetch`: downloading and converting into the staging folder, with fakes for yt-dlp."""

import importlib.util
import shutil
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from yt_dlp.utils import DownloadError

from scmp3.core import conversion_choice, download, paths, pipeline
from scmp3.core.conversion_choice import Conversion
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import Settings


def load_fakes() -> ModuleType:
    """tests/fixtures/pipeline_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_pipeline_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "pipeline_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first, since its dataclasses look their module up while it runs.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fakes = load_fakes()
record = fakes.record
TYPICAL_URL = fakes.TYPICAL_URL
TOKEN = fakes.TOKEN
FakeSoundCloud = fakes.FakeSoundCloud
recorded = fakes.recorded
prepare = fakes.prepare
save = fakes.save
staging_files = fakes.staging_files
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
world = fakes.world
# Only the download tests use these sample files.
preview_m4a = fakes.preview_m4a
low_mp3 = fakes.low_mp3


def test_fetch_downloads_the_best_stream_and_encodes_mp3_320(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    progress: list[float | None] = []
    prepared = prepare(world, settings, history)
    staged = pipeline.fetch(prepared, on_progress=lambda update: progress.append(update.pct))
    assert world.downloaded == ["hls_aac_160k"]
    assert staged.downloaded.format_id == "hls_aac_160k"
    assert staged.path == paths.staging_dir() / prepared.job_id / "audio.mp3"
    assert (staged.fmt, staged.audio.codec, staged.audio.bitrate) == ("mp3", "mp3", 320)
    assert progress == [50.0, 100.0]


def test_an_mp3_stream_is_copied_rather_than_encoded_again(
    tiny_mp3: Path, settings: Settings, history: History
) -> None:
    world = FakeSoundCloud(recorded("legacy_mp3"), {"*": tiny_mp3})
    staged = pipeline.fetch(prepare(world, settings, history))
    assert staged.downloaded.codec == "mp3"
    assert staged.path.read_bytes() == tiny_mp3.read_bytes()


def test_keeping_aac_saves_an_m4a_without_encoding(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    m4a_settings = replace(settings, audio_format="m4a")
    staged = pipeline.fetch(prepare(world, m4a_settings, history))
    assert staged.path.name == "audio.m4a"
    assert (staged.fmt, staged.audio.codec) == ("m4a", "aac")


def test_a_download_that_turns_out_to_be_a_preview_fails_as_preview_only(
    world: FakeSoundCloud, preview_m4a: Path, settings: Settings, history: History
) -> None:
    world.fixture = recorded("aac_typical", seconds=200)
    world.audio = {"*": preview_m4a}
    prepared = prepare(world, settings, history)
    with pytest.raises(ScmpError) as raised:
        pipeline.fetch(prepared)
    assert raised.value.kind == ErrorKind.preview_only


def test_a_download_that_isnt_audio_fails_as_convert_failed(
    world: FakeSoundCloud, settings: Settings, history: History, tmp_path: Path
) -> None:
    junk = tmp_path / "junk.m4a"
    junk.write_bytes(b"<html>not audio</html>" * 100)
    world.audio = {"*": junk}
    with pytest.raises(ScmpError) as raised:
        pipeline.fetch(prepare(world, settings, history))
    assert raised.value.kind == ErrorKind.convert_failed


def test_a_failed_conversion_raises_convert_failed_and_saves_nothing(
    world: FakeSoundCloud, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(src: Path, dst: Path, *, cancel: threading.Event | None = None) -> Path:
        raise ScmpError(ErrorKind.convert_failed, "ffmpeg exited with 1")

    monkeypatch.setitem(conversion_choice.ENCODERS, "mp3_320", Conversion("mp3", failing))
    with pytest.raises(ScmpError) as raised:
        save(prepare(world, settings, history), settings)
    assert raised.value.kind == ErrorKind.convert_failed
    assert not settings.library_dir.exists()


def test_cancel_during_the_download_raises_cancelled_and_leaves_no_download(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    cancel = threading.Event()
    prepared = prepare(world, settings, history)
    with pytest.raises(Cancelled):
        pipeline.fetch(prepared, on_progress=lambda update: cancel.set(), cancel=cancel)
    assert staging_files() == [prepared.job_id]


def test_a_cancel_that_arrives_during_the_conversion_still_counts(
    world: FakeSoundCloud, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()

    def converts_then_sees_the_cancel(
        src: Path, dst: Path, *, cancel: threading.Event | None = None
    ) -> Path:
        # Like copy_mp3, which can't be interrupted and finishes anyway.
        assert cancel is not None
        cancel.set()
        shutil.copyfile(src, dst)
        return dst

    replacement = Conversion("mp3", converts_then_sees_the_cancel)
    monkeypatch.setitem(conversion_choice.ENCODERS, "mp3_320", replacement)
    with pytest.raises(Cancelled):
        pipeline.fetch(prepare(world, settings, history), cancel=cancel)


def test_a_download_error_gets_the_kind_ytdlp_errors_gives_it(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    inner = record.http_error(429, "https://playback.media-streaming.soundcloud.cloud/x")
    world.download_error = DownloadError(
        "ERROR: HTTP Error 429", exc_info=(type(inner), inner, None)
    )
    with pytest.raises(ScmpError) as raised:
        pipeline.fetch(prepare(world, settings, history))
    assert raised.value.kind == ErrorKind.rate_limited


def test_stale_info_is_extracted_again_before_downloading(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    world.fixture["info"]["epoch"] = int(time.time()) - 60 * 60
    prepared = prepare(world, settings, history)
    pipeline.fetch(prepared)
    assert world.extracted == [TYPICAL_URL, TYPICAL_URL]


def test_a_signed_in_download_passes_the_token_to_yt_dlp(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    pipeline.fetch(prepare(world, settings, history, token=TOKEN))
    # One instance resolves the track, and one downloads it.
    assert world.tokens == [TOKEN, TOKEN]


def test_a_lossy_original_is_kept_when_its_bitrate_holds_up(
    tiny_m4a: Path, tiny_mp3: Path, settings: Settings, history: History
) -> None:
    # The original is 320 kbps and the best stream 160 kbps.
    world = FakeSoundCloud(recorded("original_mp3"), {"download": tiny_mp3, "*": tiny_m4a})
    staged = pipeline.fetch(prepare(world, settings, history, token=TOKEN))
    assert world.downloaded == ["download"]
    assert staged.downloaded.is_original
    assert staged.path.read_bytes() == tiny_mp3.read_bytes()


def test_a_lossy_original_below_the_stream_is_swapped_for_the_stream(
    tiny_m4a: Path, low_mp3: Path, settings: Settings, history: History
) -> None:
    world = FakeSoundCloud(recorded("original_mp3"), {"download": low_mp3, "*": tiny_m4a})
    staged = pipeline.fetch(prepare(world, settings, history, token=TOKEN))
    assert world.downloaded == ["download", "hls_aac_160k"]
    assert staged.downloaded.format_id == "hls_aac_160k"
    assert staged.audio.codec == "mp3"


def test_without_a_sign_in_the_original_is_never_tried(
    tiny_m4a: Path, settings: Settings, history: History
) -> None:
    world = FakeSoundCloud(recorded("original_wav"), {"*": tiny_m4a})
    pipeline.fetch(prepare(world, settings, history))
    assert world.downloaded == ["hls_aac_160k"]


def test_a_disk_error_in_the_staging_folder_is_about_sc_mp3s_own_data(
    world: FakeSoundCloud, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_space(job_id: str) -> Path:
        raise ScmpError(ErrorKind.disk, "can't create the staging folder: No space left on device")

    monkeypatch.setattr(download, "new_staging", no_space)
    with pytest.raises(ScmpError) as raised:
        pipeline.fetch(prepare(world, settings, history))
    assert (raised.value.kind, raised.value.where) == (ErrorKind.disk, "data")


def test_a_full_drive_during_the_download_is_about_sc_mp3s_own_data(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    inner = OSError(28, "No space left on device")
    world.download_error = DownloadError("ERROR: unable to write", exc_info=(OSError, inner, None))
    with pytest.raises(ScmpError) as raised:
        pipeline.fetch(prepare(world, settings, history))
    assert (raised.value.kind, raised.value.where) == (ErrorKind.disk, "data")
