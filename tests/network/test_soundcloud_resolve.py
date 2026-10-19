"""Live checks: resolve two public SoundCloud tracks. Run with `pytest -m network`."""

from collections.abc import Iterator

import pytest
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE

from scmp3.core.sources import soundcloud, soundcloud_formats

pytestmark = pytest.mark.network

# Public tracks that yt-dlp's own SoundCloud tests rely on: url, id, title, cover is an avatar.
TRACKS = [
    (
        "https://soundcloud.com/the80m/the-following",
        "soundcloud:tracks:343609555",
        "The Following",
        False,
    ),
    (
        "https://soundcloud.com/garyvee/sideways-prod-mad-real",
        "soundcloud:tracks:309699954",
        "Sideways (Prod. Mad Real)",
        True,
    ),
]


@pytest.fixture(autouse=True)
def shared_soundcloud_headers() -> Iterator[None]:
    """Put back yt-dlp's class-wide SoundCloud headers, so no test leaves a login behind."""
    saved = dict(SoundcloudBaseIE._HEADERS)
    yield
    SoundcloudBaseIE._HEADERS.clear()
    SoundcloudBaseIE._HEADERS.update(saved)


@pytest.mark.parametrize(("url", "track_id", "title", "is_avatar"), TRACKS)
def test_a_public_track_resolves_with_its_api_v2_fields(
    url: str, track_id: str, title: str, is_avatar: bool
) -> None:
    with soundcloud.make_ydl() as ydl:
        resolved = soundcloud.resolve_track(url, ydl)
    source = resolved.source
    assert resolved.info is not None
    assert source.id == track_id
    assert source.url == url
    assert source.title == title
    assert source.availability == "ok"
    # Only api-v2 gives the full length, so this shows the extra call worked.
    assert source.full_duration is not None
    assert source.artwork_url is not None
    assert source.artwork_is_avatar is is_avatar
    choice = soundcloud_formats.choose_format(source, signed_in=False)
    assert choice.format.codec == "aac"
    assert "_preview" not in choice.format.format_id
