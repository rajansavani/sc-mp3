"""Live checks of `scmp3 get --yes` on public SoundCloud tracks. Run with `pytest -m network`."""

from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from scmp3 import cli, cli_output
from scmp3.core.errors import ErrorKind

pytestmark = pytest.mark.network

# Public tracks that the recorded fixtures come from: url, artist folder, file name, seconds,
# kbps, has a cover.
SAVED = [
    (
        "https://soundcloud.com/the80m/the-following",
        "80M",
        "80M - The Following.mp3",
        397,
        320,
        True,
    ),
    # A legacy MP3 upload, copied rather than encoded again. It has no artwork, and the uploader
    # has SoundCloud's default avatar, so it gets no cover.
    (
        "https://soundcloud.com/thesilverlakes/burn-down-demo-2008",
        "thesilverlakes",
        "thesilverlakes - Burn Down - Demo 2008.mp3",
        277,
        128,
        False,
    ),
]
REFUSED = [
    ("https://soundcloud.com/ashburyheightsofficial/traffic-tcp-demo-2008-1", ErrorKind.drm),
    ("https://soundcloud.com/taylorswiftofficial/look-what-you-made-me-do", ErrorKind.preview_only),
]


@pytest.mark.parametrize(("url", "folder", "name", "seconds", "kbps", "has_cover"), SAVED)
def test_get_saves_a_public_track_with_its_tags_and_cover(
    url: str,
    folder: str,
    name: str,
    seconds: int,
    kbps: int,
    has_cover: bool,
    ffmpeg_exe: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["get", "--yes", "--out", str(tmp_path), url]) == cli_output.EXIT_OK
    saved = tmp_path / folder / name
    assert capsys.readouterr().out == f"{saved}\n"
    audio = MP3(saved)
    assert audio.info.length == pytest.approx(seconds, abs=2)
    assert round(audio.info.bitrate / 1000) == kbps
    tags = ID3(saved)
    assert tags.version == (2, 3, 0)
    assert str(tags["WOAS"]) == url
    assert [cover.mime for cover in tags.getall("APIC")] == (["image/jpeg"] if has_cover else [])
    assert [path.name for path in tmp_path.iterdir()] == [folder]
    assert [path.name for path in saved.parent.iterdir()] == [name]


@pytest.mark.parametrize(("url", "kind"), REFUSED)
def test_get_refuses_a_track_it_cant_save_and_leaves_no_file(
    url: str, kind: ErrorKind, ffmpeg_exe: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "library"
    assert cli.main(["get", "--yes", "--out", str(out), url]) == cli_output.EXIT_ERROR
    assert cli_output.ERROR_MESSAGES[kind] in capsys.readouterr().err
    assert not out.exists()
