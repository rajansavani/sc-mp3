"""Fakes the set tests share: a yt-dlp that answers for a recorded set and each of its tracks.

The set's listing is recorded, and each of its tracks is the typical track's fixture made over
into that track, so a whole set resolves, downloads and saves without the network. Each track's
cover is a different size, so a test can tell which cover a file got. Test files load this module
with `load_set_fakes`.
"""

import importlib.util
import io
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from mutagen.id3 import ID3
from PIL import Image

from scmp3.core import pipeline
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import Settings
from scmp3.core.urls import classify_link


def load_pipeline_fakes() -> ModuleType:
    """tests/fixtures/pipeline_fakes.py, under the name the pipeline tests load it by."""
    name = "scmp3_test_pipeline_fakes"
    if name not in sys.modules:
        path = Path(__file__).parent / "pipeline_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first, since its dataclasses look their module up while it runs.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fakes = load_pipeline_fakes()
record = fakes.record

ALBUM_URL = "https://soundcloud.com/leviryan/sets/out-of-spite"
PLAYLIST_URL = "https://soundcloud.com/non-site_records/sets/tilt-brass-bowery-aug03_ns-src02"
# The album's own cover, as its recorded listing names it.
ALBUM_COVER = "https://i1.sndcdn.com/artworks-2hmuDCrcvCzzCaXZ-1rztZA-original.jpg"
AVATAR = "https://i1.sndcdn.com/avatars-000031664596-r06vh2-original.jpg"
# The side of the set's cover, and of track N's, which is TRACK_COVER_SIDE + N.
SET_COVER_SIDE = 800
TRACK_COVER_SIDE = 600


class SetYdl(fakes.FakeYdl):
    """A FakeYdl that answers from the fixture of whichever link it's asked about."""

    world: "FakeSet"

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self.fixture = self.world.fixtures[url]
        return super().extract_info(url, download)

    def urlopen(self, request: Any) -> Any:
        if not isinstance(request, str):
            # api-v2's /resolve names the link in its query.
            [link] = parse_qs(urlsplit(request.url).query)["url"]
            self.fixture = self.world.fixtures[link]
        return super().urlopen(request)

    def process_ie_result(self, info: dict[str, Any], download: bool = True) -> dict[str, Any]:
        if info["id"] in self.world.broken_downloads:
            raise self.world.broken_downloads[info["id"]]
        return super().process_ie_result(info, download)


@dataclass
class FakeSet(fakes.FakeSoundCloud):
    """A make_ydl for a set: each YoutubeDL answers for the set and its tracks by their links."""

    fixtures: dict[str, dict[str, Any]] = field(default_factory=dict)  # by normalized link
    links: list[str] = field(default_factory=list)  # the tracks' links in set order
    # The error a track's download raises, by its yt-dlp id.
    broken_downloads: dict[str, Exception] = field(default_factory=dict)
    options: list[dict[str, Any]] = field(default_factory=list)  # each YoutubeDL's extra options

    def __call__(
        self,
        token: str | None = None,
        *,
        progress_hook: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> SetYdl:
        self.tokens.append(token)
        self.options.append(dict(extra or {}))
        return SetYdl(self, progress_hook, extra or {})

    def track(self, position: int) -> dict[str, Any]:
        """Track `position`'s fixture, counting from 1."""
        return self.fixtures[self.links[position - 1]]


def track_cover(track_id: str, size: str = "original") -> str:
    """The cover URL of a made-up set track, at a SoundCloud size such as "large"."""
    return f"https://i1.sndcdn.com/artworks-{track_id}-tr4ck-{size}.jpg"


def track_page(owner_permalink: str, track_id: str) -> str:
    """The page of a made-up set track that the listing gives only an api link for."""
    return f"https://soundcloud.com/{owner_permalink}/track-{track_id}"


def set_track(page: str, track_id: str, title: str, uploader: str) -> dict[str, Any]:
    """The typical track's fixture made over into one track of a set, with its page link."""
    fixture = fakes.recorded("aac_typical")
    fixture["url"] = page
    cover = track_cover(track_id)
    thumbnails = [{"id": "original", "url": cover, "preference": 10}]
    fixture["info"].update(
        id=track_id,
        title=title,
        uploader=uploader,
        artists=[],
        webpage_url=page,
        thumbnails=thumbnails,
    )
    fixture["api"].update(id=int(track_id), title=title, permalink_url=page, publisher_metadata={})
    return fixture


def make_set(audio: Path, name: str = "album") -> FakeSet:
    """The recorded set `name`, with each of its tracks made from the typical track."""
    listing = record.load(name, record.SETS_DIR)
    api = listing["api"]
    owner = api["user"]["username"]
    titles = {str(track["id"]): track["title"] for track in api["tracks"] if track.get("title")}
    # The tracks api-v2 describes in full list their own covers, as each fake track has one.
    for track in api["tracks"]:
        if track.get("title"):
            track["artwork_url"] = track_cover(str(track["id"]), "large")
    world = FakeSet(listing, {"*": audio}, {ALBUM_COVER: fakes.png(SET_COVER_SIDE)})
    world.fixtures[listing["url"]] = listing
    for position, item in enumerate(listing["info"]["entries"], start=1):
        _, link = classify_link(item["url"])
        is_page = urlsplit(link).hostname == "soundcloud.com"
        page = link if is_page else track_page(api["user"]["permalink"], item["id"])
        title = titles.get(item["id"], f"Track {position}")
        # Answered by either link, since a track resolved from its api link keeps its page.
        world.fixtures[link] = world.fixtures[page] = set_track(page, item["id"], title, owner)
        world.links.append(link)
        world.images[track_cover(item["id"])] = fakes.png(TRACK_COVER_SIDE + position)
    return world


def without_set_cover(world: FakeSet, *, listed_first_cover: bool = True) -> FakeSet:
    """The same set, with the owner's avatar where its own cover was, as yt-dlp gives it.

    Without `listed_first_cover`, api-v2's listing has no cover for the first track either.
    """
    world.fixture["info"]["thumbnails"] = [{"id": "original", "url": AVATAR, "preference": 10}]
    world.fixture["api"]["artwork_url"] = None
    if not listed_first_cover:
        world.fixture["api"]["tracks"][0]["artwork_url"] = None
    return world


def unresolvable(world: FakeSet, position: int) -> None:
    """Make track `position` one that was removed, so it can't be resolved."""
    missing = record.load("not_found")
    world.track(position).update(
        info=None, info_error=missing["info_error"], api=None, api_error=missing["api_error"]
    )


def prepare_set(
    world: FakeSet, settings: Settings, history: History, url: str = ALBUM_URL
) -> pipeline.PreparedSet:
    tools = pipeline.Tools(make_ydl=world, history=history)
    return pipeline.prepare_set(classify(url), settings=settings, tools=tools)


def prepare_tracks(
    world: FakeSet, settings: Settings, history: History, left_out: tuple[int, ...] = ()
) -> tuple[pipeline.PreparedSet, list[pipeline.SetTrack]]:
    """List and resolve the album, with every track but those `left_out` included."""
    prepared_set = prepare_set(world, settings, history)
    tracks = pipeline.prepare_set_tracks(prepared_set)
    for position in left_out:
        tracks[position - 1].included = False
    return prepared_set, tracks


def save_set(
    world: FakeSet,
    settings: Settings,
    history: History,
    left_out: tuple[int, ...] = (),
    **kwargs: Any,
) -> tuple[pipeline.PreparedSet, list[pipeline.TrackResult]]:
    """List, resolve and save the album, with every track but those `left_out` included."""
    prepared_set, tracks = prepare_tracks(world, settings, history, left_out)
    return prepared_set, pipeline.finalize_set(prepared_set, tracks, settings, **kwargs)


def saved_paths(results: list[pipeline.TrackResult]) -> list[Path]:
    return [result.saved.path for result in results if result.saved is not None]


def album_tags(path: Path) -> tuple[str, str, str]:
    """The album, album artist and track number an MP3 was saved with."""
    tags = ID3(path)
    return str(tags["TALB"]), str(tags["TPE2"]), str(tags["TRCK"])


def with_artists(world: FakeSet, artists: Mapping[int, str]) -> FakeSet:
    """The same set, with the publisher artist each track at a position names."""
    for position, artist in artists.items():
        world.track(position)["info"]["artists"] = [artist]
    return world


def cover_side(path: Path) -> int | None:
    """The side of the square cover embedded in an MP3, or None when it has none."""
    pictures = ID3(path).getall("APIC")
    if not pictures:
        return None
    width, height = Image.open(io.BytesIO(pictures[0].data)).size
    assert width == height
    return width


@pytest.fixture
def album(tiny_m4a: Path) -> FakeSet:
    """The recorded album with its eight tracks, each one second of AAC."""
    return make_set(tiny_m4a)
