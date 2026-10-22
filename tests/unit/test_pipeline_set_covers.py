"""Tests for the one cover an album's tracks share: the set's, the first track's or a chosen one."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from scmp3.core.history import History
from scmp3.core.models import ArtworkChoice, Settings


def load_set_fakes() -> ModuleType:
    """tests/fixtures/set_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_set_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "set_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first, since its dataclasses look their module up while it runs.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


set_fakes = load_set_fakes()
fakes = set_fakes.fakes
FakeSet = set_fakes.FakeSet
SET_COVER_SIDE = set_fakes.SET_COVER_SIDE
TRACK_COVER_SIDE = set_fakes.TRACK_COVER_SIDE
prepare_set = set_fakes.prepare_set
save_set = set_fakes.save_set
saved_paths = set_fakes.saved_paths
cover_side = set_fakes.cover_side
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
album = set_fakes.album


def test_the_album_shares_the_sets_own_cover(
    album: FakeSet, settings: Settings, history: History
) -> None:
    _, results = save_set(album, settings, history)
    assert [cover_side(path) for path in saved_paths(results)] == [SET_COVER_SIDE] * 8
    # The shared cover is downloaded once, not once per track.
    assert album.image_requests == [set_fakes.ALBUM_COVER]


def test_without_its_own_cover_the_album_shares_its_first_tracks_from_the_listing(
    album: FakeSet, settings: Settings, history: History
) -> None:
    set_fakes.without_set_cover(album)
    # The listing has it before any track resolves.
    first_track_id = album.fixture["info"]["entries"][0]["id"]
    playlist = prepare_set(album, settings, history).playlist
    assert playlist.artwork_url == set_fakes.track_cover(first_track_id, "t500x500")
    _, results = save_set(album, settings, history)
    assert [cover_side(path) for path in saved_paths(results)] == [TRACK_COVER_SIDE + 1] * 8


def test_the_first_tracks_cover_is_shared_even_when_it_is_left_out(
    album: FakeSet, settings: Settings, history: History
) -> None:
    # SoundCloud shows the first track's cover for a set without one, so the album keeps it.
    set_fakes.without_set_cover(album)
    _, results = save_set(album, settings, history, left_out=(1,))
    assert [cover_side(path) for path in saved_paths(results)] == [TRACK_COVER_SIDE + 1] * 7


def test_the_first_tracks_cover_is_shared_even_when_it_cant_be_resolved(
    album: FakeSet, settings: Settings, history: History
) -> None:
    # As with a first track that's DRM-protected.
    set_fakes.without_set_cover(album)
    set_fakes.unresolvable(album, 1)
    _, results = save_set(album, settings, history)
    assert [cover_side(path) for path in saved_paths(results)] == [TRACK_COVER_SIDE + 1] * 7


def test_with_no_cover_listed_the_album_shares_the_first_resolved_tracks(
    album: FakeSet, settings: Settings, history: History
) -> None:
    set_fakes.without_set_cover(album, listed_first_cover=False)
    set_fakes.unresolvable(album, 1)
    _, results = save_set(album, settings, history)
    assert [cover_side(path) for path in saved_paths(results)] == [TRACK_COVER_SIDE + 2] * 7


def test_a_chosen_cover_replaces_the_sets_own(
    album: FakeSet, settings: Settings, history: History, tmp_path: Path
) -> None:
    picked = tmp_path / "picked.png"
    picked.write_bytes(fakes.png(640))
    _, results = save_set(album, settings, history, cover=ArtworkChoice("file", str(picked)))
    assert {cover_side(path) for path in saved_paths(results)} == {640}


def test_a_cover_that_wont_load_leaves_every_track_without_one(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.images.clear()
    _, results = save_set(album, settings, history)
    assert [result.outcome for result in results] == ["saved"] * 8
    assert all(result.saved is not None and result.saved.cover_missing for result in results)
    assert {cover_side(path) for path in saved_paths(results)} == {None}
