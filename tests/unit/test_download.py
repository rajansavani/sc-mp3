"""Tests for downloading into a staging folder and for the staging helpers, without the internet."""

import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mutagen.mp4 import MP4
from yt_dlp.utils import DownloadError

from scmp3.core import download as dl
from scmp3.core import engine
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.models import Progress

NOW = 1_800_000_000.0
FORMAT_ID = "hls_aac_160k"


def track_info(**changes: Any) -> dict[str, Any]:
    """A small info dict, as extract_info returns it, extracted just now."""
    info: dict[str, Any] = {
        "id": "123",
        "title": "Night Drive",
        "ext": "m4a",
        "epoch": int(NOW),
        "formats": [{"format_id": FORMAT_ID, "ext": "m4a", "url": "https://example.invalid/a"}],
    }
    info.update(changes)
    return info


def downloading(done: int, total: int | None = 3000, **extra: Any) -> dict[str, Any]:
    """A yt-dlp progress dict for a download that's under way."""
    return {"status": "downloading", "downloaded_bytes": done, "total_bytes": total, **extra}


class FakeYoutubeDL:
    """Stands in for YoutubeDL: writes a file where outtmpl says, calling the hook as it goes."""

    def __init__(self, factory: "FakeFactory", hook: Any, extra: Mapping[str, Any]) -> None:
        self.factory = factory
        self.hook = hook
        self.params = dict(extra)
        self.processed: list[dict[str, Any]] = []
        # The staging folder's files when the download started.
        self.files_at_start: list[str] = []
        self.closed = False

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True

    def process_ie_result(self, info: dict[str, Any], download: bool = True) -> dict[str, Any]:
        assert download is True
        self.processed.append(info)
        template = self.params["outtmpl"]
        path = Path(template.replace("%(ext)s", info["ext"]).replace("%%", "%"))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.files_at_start = sorted(item.name for item in path.parent.iterdir())
        with path.open("wb") as file:
            for status in self.factory.statuses:
                file.write(b"\0" * 1000)
                self.hook(status)
        self.hook({"status": "finished", "downloaded_bytes": 3000, "total_bytes": 3000})
        for decoy in self.factory.decoys:
            # A newer file in the folder, which a folder scan would wrongly pick.
            (path.parent / decoy).write_bytes(b"decoy")
        if self.factory.reported_name:
            # yt-dlp's fixups can rename the file, and only requested_downloads says so.
            path = path.replace(path.with_name(self.factory.reported_name))
        downloads = [] if self.factory.report_nothing else [{"filepath": str(path)}]
        return {**info, "requested_downloads": downloads}


@dataclass
class FakeFactory:
    """A ydl_factory that hands out FakeYoutubeDL instances and remembers them."""

    statuses: list[dict[str, Any]] = field(
        default_factory=lambda: [downloading(1000), downloading(2000)]
    )
    decoys: list[str] = field(default_factory=list)
    reported_name: str | None = None
    report_nothing: bool = False
    made: list[FakeYoutubeDL] = field(default_factory=list)

    def __call__(
        self, *, progress_hook: Any = None, extra: Mapping[str, Any] | None = None
    ) -> FakeYoutubeDL:
        ydl = FakeYoutubeDL(self, progress_hook, extra or {})
        self.made.append(ydl)
        return ydl

    @property
    def ydl(self) -> FakeYoutubeDL:
        [only] = self.made
        return only


def run_download(
    staging: Path, factory: FakeFactory, info: dict[str, Any] | None = None, **kwargs: Any
) -> Path:
    """Call download() with the fake factory and a clock fixed at NOW."""
    return dl.download(
        info if info is not None else track_info(),
        FORMAT_ID,
        staging,
        ydl_factory=factory,
        clock=lambda: NOW,
        **kwargs,
    )


def test_download_returns_the_file_yt_dlp_reports(tmp_path: Path) -> None:
    factory = FakeFactory()
    path = run_download(tmp_path, factory)
    assert path == tmp_path / "source.m4a"
    assert path.stat().st_size == 2000


def test_the_path_comes_from_requested_downloads_not_from_the_folder(tmp_path: Path) -> None:
    factory = FakeFactory(reported_name="source.fixed.m4a", decoys=["zzz-newest.m4a"])
    path = run_download(tmp_path, factory)
    assert path == tmp_path / "source.fixed.m4a"


def test_download_asks_for_the_format_and_the_source_template(tmp_path: Path) -> None:
    factory = FakeFactory()
    run_download(tmp_path, factory)
    params = factory.ydl.params
    assert params["format"] == FORMAT_ID
    assert params["outtmpl"] == str(tmp_path / "source.%(ext)s")
    assert params["overwrites"] is True
    assert params["skip_unavailable_fragments"] is False


def source_files(folder: Path) -> list[str]:
    """The names of the source.* files in a folder."""
    return sorted(path.name for path in folder.glob("source.*"))


LEFTOVERS = [
    "source.m4a",
    "source.m4a.part",
    "source.m4a.part-Frag12",
    "source.m4a.ytdl",
    "source.mp3",
    "source.temp.m4a",
]


def test_what_an_earlier_download_left_is_deleted_first(tmp_path: Path) -> None:
    for name in LEFTOVERS:
        (tmp_path / name).write_bytes(b"old")
    # The caller's own files have other names and stay.
    (tmp_path / "track.mp3").write_bytes(b"converted")
    factory = FakeFactory()
    run_download(tmp_path, factory)
    assert factory.ydl.files_at_start == ["track.mp3"]
    assert (tmp_path / "track.mp3").read_bytes() == b"converted"


def test_a_leftover_that_cant_be_deleted_stops_the_download(tmp_path: Path) -> None:
    # unlink refuses a folder on every OS, as Windows refuses a file that's open.
    (tmp_path / "source.m4a.part").mkdir()
    factory = FakeFactory()
    with pytest.raises(ScmpError) as error_info:
        run_download(tmp_path, factory)
    assert error_info.value.kind is ErrorKind.disk
    assert error_info.value.detail is not None
    assert error_info.value.detail.startswith("can't delete source.m4a.part from the staging")
    assert factory.made == []


def test_a_percent_sign_in_the_staging_folder_is_escaped(tmp_path: Path) -> None:
    staging = tmp_path / "100% mix"
    assert dl.source_template(staging) == str(tmp_path / "100%% mix" / "source.%(ext)s")
    assert run_download(staging, FakeFactory()) == staging / "source.m4a"


def test_the_yt_dlp_instance_is_closed_afterwards(tmp_path: Path) -> None:
    factory = FakeFactory()
    run_download(tmp_path, factory)
    assert factory.ydl.closed


def test_progress_is_reported_as_progress_models(tmp_path: Path) -> None:
    factory = FakeFactory(statuses=[downloading(1000, speed=500.0, eta=4), downloading(2000)])
    updates: list[Progress] = []
    run_download(tmp_path, factory, on_progress=updates.append)
    assert updates[0] == Progress(
        pct=pytest.approx(100 / 3), speed=500.0, eta=4, downloaded=1000, total=3000
    )
    assert updates[-1] == Progress(pct=100.0, downloaded=3000, total=3000)
    assert updates[-1].to_dict() == {
        "pct": 100.0,
        "speed": None,
        "eta": None,
        "downloaded": 3000,
        "total": 3000,
    }


@pytest.mark.parametrize(
    ("status", "pct", "total"),
    [
        (downloading(500, total=2000), 25.0, 2000),
        # HLS downloads only have an estimate, which yt-dlp gives as a float.
        (downloading(500, total=None, total_bytes_estimate=1000.0), 50.0, 1000),
        (downloading(500, total=None, fragment_index=3, fragment_count=4), 75.0, None),
        (downloading(500, total=None), None, None),
        # An estimate can fall short of the bytes so far.
        (downloading(1500, total=None, total_bytes_estimate=1000.0), 100.0, 1000),
    ],
)
def test_progress_uses_the_best_size_yt_dlp_knows(
    status: dict[str, Any], pct: float | None, total: int | None
) -> None:
    progress = dl.progress_from(status)
    assert progress.pct == pct
    assert progress.total == total
    assert progress.downloaded == status["downloaded_bytes"]


def test_cancelling_mid_download_raises_cancelled(tmp_path: Path) -> None:
    cancel = threading.Event()
    factory = FakeFactory(statuses=[downloading(1000), downloading(2000), downloading(3000)])
    updates: list[Progress] = []

    def cancel_after_first_update(progress: Progress) -> None:
        updates.append(progress)
        cancel.set()

    with pytest.raises(Cancelled):
        run_download(tmp_path, factory, on_progress=cancel_after_first_update, cancel=cancel)
    assert len(updates) == 1
    assert factory.ydl.closed
    assert source_files(tmp_path) == []


def test_a_cancel_before_the_start_downloads_nothing(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    factory = FakeFactory()
    with pytest.raises(Cancelled):
        run_download(tmp_path, factory, cancel=cancel)
    assert factory.made == []


def test_a_cancel_after_the_last_update_still_cancels(tmp_path: Path) -> None:
    cancel = threading.Event()

    def cancel_when_finished(progress: Progress) -> None:
        if progress.pct == 100.0:
            cancel.set()

    with pytest.raises(Cancelled):
        run_download(tmp_path, FakeFactory(), on_progress=cancel_when_finished, cancel=cancel)
    # The finished file goes too, so a cancel never leaves a source file.
    assert source_files(tmp_path) == []


def test_stale_info_is_extracted_again_first(tmp_path: Path) -> None:
    factory = FakeFactory()
    fresh = track_info(title="Fresh copy", epoch=int(NOW))
    calls: list[str] = []

    def reextract() -> dict[str, Any]:
        calls.append("reextract")
        return fresh

    old = track_info(epoch=int(NOW) - dl.STALE_AFTER - 1)
    run_download(tmp_path, factory, info=old, reextract=reextract)
    assert calls == ["reextract"]
    assert factory.ydl.processed[0]["title"] == "Fresh copy"


def test_info_younger_than_ten_minutes_is_used_as_it_is(tmp_path: Path) -> None:
    factory = FakeFactory()

    def reextract() -> dict[str, Any]:
        raise AssertionError("fresh info was extracted again")

    info = track_info(epoch=int(NOW) - dl.STALE_AFTER + 5)
    run_download(tmp_path, factory, info=info, reextract=reextract)
    assert factory.ydl.processed[0]["title"] == "Night Drive"


def test_info_without_a_timestamp_counts_as_stale(tmp_path: Path) -> None:
    info = track_info()
    del info["epoch"]
    fresh = track_info(title="Fresh copy")
    factory = FakeFactory()
    run_download(tmp_path, factory, info=info, reextract=lambda: fresh)
    assert factory.ydl.processed[0]["title"] == "Fresh copy"


def test_stale_info_without_a_way_to_refresh_it_is_used_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    factory = FakeFactory()
    old = track_info(epoch=int(NOW) - 3600)
    with caplog.at_level(logging.WARNING, logger=dl.__name__):
        run_download(tmp_path, factory, info=old)
    assert factory.ydl.processed[0]["id"] == "123"
    assert "too old" in caplog.text


def test_an_earlier_format_choice_is_dropped_and_the_callers_info_is_unchanged(
    tmp_path: Path,
) -> None:
    info = track_info(
        requested_formats=[{"format_id": "video"}, {"format_id": "audio"}],
        requested_downloads=[{"filepath": "old.m4a"}],
        __private="x",
    )
    before = {key: value for key, value in info.items()}
    factory = FakeFactory()
    run_download(tmp_path, factory, info=info)
    processed = factory.ydl.processed[0]
    for dropped in ("requested_formats", "requested_downloads", "__private"):
        assert dropped not in processed
    assert processed["formats"] == info["formats"]
    assert info == before


def test_no_requested_downloads_means_the_engine_needs_an_update(tmp_path: Path) -> None:
    with pytest.raises(ScmpError) as error_info:
        run_download(tmp_path, FakeFactory(report_nothing=True))
    assert error_info.value.kind is ErrorKind.engine_outdated


def test_a_reported_file_that_doesnt_exist_means_the_engine_needs_an_update(
    tmp_path: Path,
) -> None:
    result = {"requested_downloads": [{"filepath": str(tmp_path / "gone.m4a")}]}
    with pytest.raises(ScmpError) as error_info:
        dl.downloaded_path(result)
    assert error_info.value.kind is ErrorKind.engine_outdated
    assert error_info.value.detail == "yt-dlp reported gone.m4a, but it's missing"


# Two different files, so a download that mixes them shows up.
AUDIO_A = bytes(range(256)) * 1024  # 256 KiB
AUDIO_B = bytes(reversed(range(256))) * 1024
BODIES = {"/a.mp3": AUDIO_A, "/b.mp3": AUDIO_B}


def range_start(header: str | None) -> int:
    """Where a `Range: bytes=<start>-` request starts, or 0 without one."""
    if not header or not header.startswith("bytes="):
        return 0
    return int(header.removeprefix("bytes=").split("-")[0] or 0)


class AudioHandler(BaseHTTPRequestHandler):
    """Serves BODIES in small pieces, so yt-dlp calls its progress hook many times.

    It honors Range, as real servers do, so a resumed download would show up.
    """

    def do_GET(self) -> None:
        body = BODIES[self.path]
        start = range_start(self.headers.get("Range"))
        if start:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        piece = 16 * 1024
        try:
            for offset in range(start, len(body), piece):
                self.wfile.write(body[offset : offset + piece])
                self.wfile.flush()
                time.sleep(0.01)
        except ConnectionError:
            # The client hung up, as a cancelled download does.
            return

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the server quiet in test output."""


@contextlib.contextmanager
def serving(handler: Callable[..., BaseHTTPRequestHandler]) -> Iterator[str]:
    """Run an HTTP server on this machine only, and give its address."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def audio_server() -> Iterator[str]:
    """A local server for the two audio files."""
    with serving(AudioHandler) as address:
        yield address


def local_info(server: str, name: str = "a") -> dict[str, Any]:
    """Info for a track whose only format is one of the local server's files."""
    return {
        "id": f"local-{name}",
        "title": "Local test",
        "extractor": "generic",
        "extractor_key": "Generic",
        "webpage_url": f"{server}/track",
        "epoch": int(time.time()),
        "formats": [
            {
                "format_id": f"http_mp3_{name}",
                "url": f"{server}/{name}.mp3",
                "ext": "mp3",
                "acodec": "mp3",
                "vcodec": "none",
            }
        ],
    }


def local_ydl(*, progress_hook: Any = None, extra: Mapping[str, Any] | None = None) -> Any:
    """The real make_ydl, with a direct connection whatever proxy the machine has set."""
    return engine.make_ydl(progress_hook=progress_hook, extra={**(extra or {}), "proxy": ""})


def cancel_partway(cancel: threading.Event, updates: list[Progress]) -> Callable[[Progress], None]:
    """An on_progress that records each update and cancels once 30% is done."""

    def on_progress(progress: Progress) -> None:
        updates.append(progress)
        if progress.pct is not None and progress.pct >= 30:
            cancel.set()

    return on_progress


def test_a_real_youtubedl_downloads_into_the_staging_folder(
    tmp_path: Path, audio_server: str
) -> None:
    updates: list[Progress] = []
    path = dl.download(
        local_info(audio_server),
        "http_mp3_a",
        tmp_path,
        ydl_factory=local_ydl,
        on_progress=updates.append,
    )
    assert path == tmp_path / "source.mp3"
    assert path.read_bytes() == AUDIO_A
    assert updates[-1].pct == 100.0
    assert updates[-1].total == len(AUDIO_A)


def test_a_real_youtubedl_cancelled_mid_download_raises_cancelled_and_leaves_nothing(
    tmp_path: Path, audio_server: str
) -> None:
    cancel = threading.Event()
    updates: list[Progress] = []
    with pytest.raises(Cancelled):
        dl.download(
            local_info(audio_server),
            "http_mp3_a",
            tmp_path,
            ydl_factory=local_ydl,
            on_progress=cancel_partway(cancel, updates),
            cancel=cancel,
        )
    assert updates[-1].pct is not None and updates[-1].pct < 100
    assert source_files(tmp_path) == []


def test_a_partial_download_left_behind_isnt_resumed_into_another_format(
    tmp_path: Path, audio_server: str
) -> None:
    # What a failed download, or an app closed mid-download, leaves behind.
    (tmp_path / "source.mp3.part").write_bytes(AUDIO_A[: 64 * 1024])
    path = dl.download(local_info(audio_server, "b"), "http_mp3_b", tmp_path, ydl_factory=local_ydl)
    assert path.read_bytes() == AUDIO_B


def test_after_a_cancel_another_format_downloads_on_its_own(
    tmp_path: Path, audio_server: str
) -> None:
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        dl.download(
            local_info(audio_server, "a"),
            "http_mp3_a",
            tmp_path,
            ydl_factory=local_ydl,
            on_progress=cancel_partway(cancel, []),
            cancel=cancel,
        )
    path = dl.download(local_info(audio_server, "b"), "http_mp3_b", tmp_path, ydl_factory=local_ydl)
    assert path.read_bytes() == AUDIO_B


HLS_SECONDS = 20


@pytest.fixture(scope="module")
def hls_folder(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An HLS playlist of AAC in 2-second segments, the way SoundCloud streams."""
    folder = tmp_path_factory.mktemp("hls")
    command = [
        str(ffmpeg_exe),
        *("-hide_banner", "-loglevel", "error", "-nostdin"),
        *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", str(HLS_SECONDS)),
        *("-c:a", "aac", "-b:a", "128k"),
        *("-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod"),
        *("-hls_segment_filename", str(folder / "seg%03d.ts")),
        str(folder / "index.m3u8"),
    ]
    subprocess.run(command, check=True)
    return folder


def hls_handler(
    folder: Path, missing: frozenset[str] = frozenset()
) -> type[BaseHTTPRequestHandler]:
    """A handler that serves the HLS folder and answers 404 for the paths in `missing`."""

    class HlsHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(folder), **kwargs)

        def do_GET(self) -> None:
            if self.path in missing:
                self.send_error(404)
                return
            super().do_GET()

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the server quiet in test output."""

    return HlsHandler


def hls_info(server: str) -> dict[str, Any]:
    """Info for a track whose only format is the local HLS stream, shaped like SoundCloud's."""
    return {
        "id": "hls-1",
        "title": "HLS test",
        "extractor": "generic",
        "extractor_key": "Generic",
        "webpage_url": f"{server}/track",
        "epoch": int(time.time()),
        "formats": [
            {
                "format_id": "hls_aac_160k",
                "url": f"{server}/index.m3u8",
                "ext": "m4a",
                "protocol": "m3u8_native",
                "container": "m4a_dash",
                "acodec": "aac",
                "vcodec": "none",
            }
        ],
    }


def test_a_real_hls_download_gives_the_fixed_up_m4a(hls_folder: Path, tmp_path: Path) -> None:
    with serving(hls_handler(hls_folder)) as server:
        path = dl.download(hls_info(server), "hls_aac_160k", tmp_path, ydl_factory=local_ydl)
    assert path == tmp_path / "source.m4a"
    mp4 = MP4(path)
    assert mp4.info.codec == "mp4a.40.2"
    assert mp4.info.length == pytest.approx(HLS_SECONDS, abs=0.5)
    assert source_files(tmp_path) == ["source.m4a"]


def test_a_cancelled_hls_download_leaves_a_folder_that_can_be_deleted(hls_folder: Path) -> None:
    staging = dl.new_staging("hls-job")
    cancel = threading.Event()
    updates: list[Progress] = []
    with serving(hls_handler(hls_folder)) as server, pytest.raises(Cancelled):
        dl.download(
            hls_info(server),
            "hls_aac_160k",
            staging,
            ydl_factory=local_ydl,
            on_progress=cancel_partway(cancel, updates),
            cancel=cancel,
        )
    assert updates[-1].pct is not None and updates[-1].pct < 100
    assert source_files(staging) == []
    # On Windows this fails while yt-dlp still holds its partial files open.
    assert dl.cleanup("hls-job") is True


def test_an_hls_segment_that_keeps_failing_fails_the_download(
    hls_folder: Path, tmp_path: Path
) -> None:
    handler = hls_handler(hls_folder, missing=frozenset({"/seg004.ts"}))
    with serving(handler) as server, pytest.raises(DownloadError):
        dl.download(hls_info(server), "hls_aac_160k", tmp_path, ydl_factory=local_ydl)


def test_new_staging_creates_the_job_folder_under_the_cache(scmp3_home: Path) -> None:
    folder = dl.new_staging("job-1")
    assert folder == scmp3_home / "cache" / "staging" / "job-1"
    assert folder.is_dir()
    # Calling it again for the same job is fine.
    assert dl.new_staging("job-1") == folder


def test_cleanup_deletes_the_job_folder_and_its_files() -> None:
    folder = dl.new_staging("job-2")
    (folder / "source.m4a").write_bytes(b"audio")
    (folder / "nested").mkdir()
    (folder / "nested" / "part").write_bytes(b"x")
    assert dl.cleanup("job-2") is True
    assert not folder.exists()


def test_cleanup_of_a_folder_that_is_already_gone_succeeds() -> None:
    assert dl.cleanup("never-made") is True


def test_cleanup_that_cant_delete_warns_and_returns_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dl.new_staging("job-3")

    def locked(path: Path) -> None:
        raise PermissionError(13, "The file is in use")

    monkeypatch.setattr(dl.shutil, "rmtree", locked)
    with caplog.at_level(logging.WARNING, logger=dl.__name__):
        assert dl.cleanup("job-3") is False
    assert "job-3" in caplog.text


def test_a_cleanup_error_without_an_os_reason_gives_its_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dl.new_staging("job-link")

    def refuse_link(path: Path) -> None:
        # rmtree raises this, with no strerror, when the folder is a link.
        raise OSError("Cannot call rmtree on a symbolic link")

    monkeypatch.setattr(dl.shutil, "rmtree", refuse_link)
    with caplog.at_level(logging.WARNING, logger=dl.__name__):
        assert dl.cleanup("job-link") is False
    assert "job-link: Cannot call rmtree on a symbolic link" in caplog.text
    assert "None" not in caplog.text


@pytest.mark.parametrize("job_id", ["", "..", ".", "a/b", "a\\b", "C:", "job 1"])
def test_job_ids_that_arent_plain_folder_names_are_refused(job_id: str) -> None:
    with pytest.raises(ValueError):
        dl.new_staging(job_id)
    with pytest.raises(ValueError):
        dl.cleanup(job_id)


def make_aged_folder(name: str, age_days: float) -> Path:
    """A staging folder whose last change was `age_days` days before NOW."""
    folder = dl.new_staging(name)
    (folder / "source.m4a").write_bytes(b"audio")
    stamp = NOW - age_days * 24 * 60 * 60
    os.utime(folder, (stamp, stamp))
    return folder


def test_purge_old_deletes_old_folders_except_the_ones_to_keep() -> None:
    old = make_aged_folder("old", 8)
    restored = make_aged_folder("restored", 30)
    recent = make_aged_folder("recent", 6)
    purged = dl.purge_old(keep={"restored"}, clock=lambda: NOW)
    assert purged == ["old"]
    assert not old.exists()
    assert restored.is_dir()
    assert recent.is_dir()


def test_purge_old_takes_the_age_in_days() -> None:
    make_aged_folder("two-days", 2)
    assert dl.purge_old(days=1, clock=lambda: NOW) == ["two-days"]


def test_purge_old_leaves_stray_files_alone(scmp3_home: Path) -> None:
    make_aged_folder("old", 8)
    stray = scmp3_home / "cache" / "staging" / "notes.txt"
    stray.write_text("not a job")
    os.utime(stray, (0, 0))
    assert dl.purge_old(clock=lambda: NOW) == ["old"]
    assert stray.exists()


def test_purge_old_without_a_staging_folder_does_nothing() -> None:
    assert dl.purge_old(clock=lambda: NOW) == []
