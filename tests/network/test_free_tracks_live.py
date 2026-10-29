"""Live checks of `scmp3 get --yes` on freely licensed links. The nightly canary runs these.

Run with `pytest -m network`. YouTube needs Deno, so run `uv sync --locked --extra deno` first.
On GitHub's runners, YouTube's bot check skips the YouTube test. Anywhere else it fails it.
"""

import io
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from PIL import Image

from scmp3 import cli, cli_output
from scmp3.core.errors import ErrorKind
from scmp3.core.sources.deno import find_deno

pytestmark = pytest.mark.network

# SoundCloud once served only 100x100 artwork for two months, so a smaller cover is a failure.
MIN_COVER_SIDE = 500
# YouTube bot-checks datacenter IPs like the runners', and sc-mp3 sends no cookies to get past it.
RUNNER_BOT_CHECK = (
    "YouTube asked GitHub's runner to prove it isn't a bot. "
    "The YouTube check runs only on a home connection."
)


@dataclass(frozen=True)
class FreeTrack:
    """A freely licensed upload, and what its saved MP3 should hold."""

    url: str
    artist: str
    # Only the start of the title is checked, so changes to title cleanup don't fail the canary.
    title_start: str
    year: str
    seconds: int
    kbps: int
    source_id: str


# CC BY uploads on the artists' own accounts, which have offered their music free for years.
SOUNDCLOUD_TRACKS = [
    # An AAC 160 kbps stream with its own artwork, encoded again at 320 kbps.
    FreeTrack(
        url="https://soundcloud.com/kevin-9-1/carefree",
        artist="Kevin MacLeod",
        title_start="Carefree",
        year="2014",
        seconds=205,
        kbps=320,
        source_id="soundcloud:tracks:163473885",
    ),
    # A link whose slug repeats the artist, as many uploads do.
    FreeTrack(
        url="https://soundcloud.com/broke-for-free/broke-for-free-juparo",
        artist="Broke For Free",
        title_start="Juparo",
        year="2013",
        seconds=248,
        kbps=320,
        source_id="soundcloud:tracks:112431474",
    ),
    # A legacy MP3 upload, copied at its own 128 kbps rather than encoded again.
    FreeTrack(
        url="https://soundcloud.com/joshwoodward/let-it-in",
        artist="Josh Woodward Music",
        title_start="Let It In",
        year="2014",
        seconds=242,
        kbps=128,
        source_id="soundcloud:tracks:150591777",
    ),
]
# On Kevin MacLeod's official channel, marked CC BY by YouTube.
YOUTUBE_VIDEO = FreeTrack(
    url="https://www.youtube.com/watch?v=Yy-NyPTRYU8",
    artist="Kevin MacLeod",
    title_start="Exit the Premises",
    year="2015",
    seconds=212,
    kbps=320,
    source_id="youtube:Yy-NyPTRYU8",
)


@pytest.fixture
def deno() -> None:
    """Fail rather than skip without Deno, so the canary can't pass with YouTube untested."""
    if find_deno() is None:
        pytest.fail("YouTube needs Deno. Run `uv sync --locked --extra deno` first.")


@pytest.mark.parametrize("track", SOUNDCLOUD_TRACKS, ids=lambda track: track.source_id)
def test_a_free_soundcloud_track_saves_with_its_tags_length_and_cover(
    track: FreeTrack, ffmpeg_exe: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    check_saved(track, save(track.url, tmp_path, capsys), tmp_path)


def test_a_free_youtube_video_saves_with_its_tags_length_and_cover(
    deno: None, ffmpeg_exe: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = get(YOUTUBE_VIDEO.url, tmp_path)
    output = capsys.readouterr()
    if exit_code == cli_output.EXIT_ERROR and on_github_runner() and is_bot_check(output.err):
        skip_with_summary(RUNNER_BOT_CHECK)
    assert exit_code == cli_output.EXIT_OK
    check_saved(YOUTUBE_VIDEO, Path(output.out.strip()), tmp_path)


def get(url: str, library: Path) -> int:
    """Run `scmp3 get --yes` on one link and return its exit code."""
    return cli.main(["get", "--yes", "--out", str(library), url])


def save(url: str, library: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Run `scmp3 get --yes` on one link and return the file it saved."""
    assert get(url, library) == cli_output.EXIT_OK
    # Progress goes to stderr, so stdout holds only the saved file's path.
    return Path(capsys.readouterr().out.strip())


def on_github_runner() -> bool:
    """Whether this runs on GitHub Actions, which sets GITHUB_ACTIONS on every runner."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def is_bot_check(stderr: str) -> bool:
    """Whether `scmp3 get` failed because YouTube asked it to prove it isn't a bot."""
    return cli_output.ERROR_MESSAGES[ErrorKind.bot_check] in stderr


def skip_with_summary(reason: str) -> None:
    """Skip the test, and write the reason in the GitHub job summary too."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(f"Skipped the YouTube test. {reason}\n")
    pytest.skip(reason)


def check_saved(track: FreeTrack, saved: Path, library: Path) -> None:
    """Check the saved MP3's place, length, bitrate, tags and cover."""
    assert saved.parent == library / track.artist
    assert [path for path in library.rglob("*") if path.is_file()] == [saved]
    audio = MP3(saved)
    assert audio.info.length == pytest.approx(track.seconds, abs=2)
    assert round(audio.info.bitrate / 1000) == track.kbps
    tags = ID3(saved)
    assert tags.version == (2, 3, 0)
    assert str(tags["TPE1"]) == track.artist
    assert str(tags["TIT2"]).startswith(track.title_start)
    assert str(tags["TDRC"]) == track.year
    assert str(tags["WOAS"]) == track.url
    assert str(tags["TXXX:SCMP3_SOURCE_ID"]) == track.source_id
    [cover] = tags.getall("APIC")
    assert cover.mime == "image/jpeg"
    width, height = Image.open(io.BytesIO(cover.data)).size
    assert width == height >= MIN_COVER_SIDE
