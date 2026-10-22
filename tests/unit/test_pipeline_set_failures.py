"""Tests for a set's tracks that fail, saving the set again, its staging folders, and saving one
track of a set alone."""

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from mutagen.id3 import ID3

from scmp3.core import paths, pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import Settings
from scmp3.core.pipeline import saving
from scmp3.core.placing import atomic_place


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
prepare_tracks = set_fakes.prepare_tracks
save_set = set_fakes.save_set
saved_paths = set_fakes.saved_paths
album_tags = set_fakes.album_tags
cover_side = set_fakes.cover_side
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
album = set_fakes.album

# The third track: its yt-dlp id, and its name in the artist's folder.
THIRD_ID = "1378981744"
THIRD_NAME = "Levi Ryan - silkroad w-LONEWOLFPRIE.mp3"


def offline() -> ScmpError:
    return ScmpError(ErrorKind.offline, "the connection dropped")


def failing_once(name: str) -> Callable[..., Path]:
    """atomic_place, failing as on a full disk the first time it's asked to place `name`."""
    failed: list[Path] = []

    def place(src: Path, dest: Path, **kwargs: Any) -> Path:
        if dest.name == name and not failed:
            failed.append(dest)
            raise ScmpError(ErrorKind.disk, "no space left on the disk")
        return atomic_place(src, dest, **kwargs)

    return place


def staging_of(track: pipeline.SetTrack) -> Path:
    assert track.prepared is not None
    return paths.staging_dir() / track.prepared.job_id


def test_one_track_that_fails_to_download_doesnt_stop_the_rest(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.broken_downloads[THIRD_ID] = offline()
    _, results = save_set(album, settings, history)
    assert [result.outcome for result in results] == ["saved"] * 2 + ["failed"] + ["saved"] * 5
    assert results[2].error is not None
    assert results[2].error.kind == ErrorKind.offline
    # Its number was given out before the save, so it keeps it and the others keep theirs.
    assert (results[2].track_no, results[2].track_total) == (3, 8)
    assert [album_tags(path)[2] for path in saved_paths(results)] == [
        "1/8",
        "2/8",
        "4/8",
        "5/8",
        "6/8",
        "7/8",
        "8/8",
    ]


def test_a_track_that_couldnt_be_resolved_fails_without_taking_a_number(
    album: FakeSet, settings: Settings, history: History
) -> None:
    set_fakes.unresolvable(album, 3)
    _, results = save_set(album, settings, history)
    assert results[2].outcome == "failed"
    assert results[2].error is not None
    assert results[2].error.kind == ErrorKind.unavailable
    assert results[2].track_no is None
    assert [album_tags(path)[2] for path in saved_paths(results)] == [
        f"{number}/7" for number in range(1, 8)
    ]


def test_a_taken_name_the_setting_skips_is_reported_and_the_rest_save(
    album: FakeSet, settings: Settings, history: History
) -> None:
    settings = replace(settings, when_file_exists="skip")
    taken = settings.library_dir / "Levi Ryan" / "Levi Ryan - purextc.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"already here")
    _, results = save_set(album, settings, history)
    assert results[3].outcome == "name_taken"
    assert taken.read_bytes() == b"already here"
    assert [result.outcome for result in results].count("saved") == 7


def test_saving_the_set_again_saves_only_the_failed_track_with_its_number(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.broken_downloads[THIRD_ID] = offline()
    prepared_set, tracks = prepare_tracks(album, settings, history)
    first = pipeline.finalize_set(prepared_set, tracks, settings)
    del album.broken_downloads[THIRD_ID]
    downloads = len(album.downloaded)
    again = pipeline.finalize_set(prepared_set, tracks, settings)
    assert [result.outcome for result in again] == ["saved"] * 8
    # Only the failed track downloads, and it saves as track 3 of 8.
    assert len(album.downloaded) == downloads + 1
    assert again[2].saved is not None
    assert album_tags(again[2].saved.path)[2] == "3/8"
    # The others aren't saved twice, though keep_both would keep a second copy of each.
    assert saved_paths(again)[:2] == saved_paths(first)[:2]
    assert len(list((settings.library_dir / "Levi Ryan").iterdir())) == 8


def test_a_track_whose_save_fails_keeps_its_audio_for_saving_again(
    album: FakeSet, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(saving, "atomic_place", failing_once(THIRD_NAME))
    prepared_set, tracks = prepare_tracks(album, settings, history)
    first = pipeline.finalize_set(prepared_set, tracks, settings)
    assert first[2].outcome == "failed"
    staged = tracks[2].staged
    assert staged is not None
    assert staged.path.is_file()
    downloads = len(album.downloaded)
    again = pipeline.finalize_set(prepared_set, tracks, settings)
    assert again[2].saved is not None
    assert again[2].saved.path.name == THIRD_NAME
    assert len(album.downloaded) == downloads
    assert tracks[2].staged is None


def test_staging_goes_after_a_save_or_a_skipped_name_and_stays_otherwise(
    album: FakeSet, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(settings, when_file_exists="skip")
    taken = settings.library_dir / "Levi Ryan" / "Levi Ryan - matrix w-david shawty.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"already here")
    monkeypatch.setattr(saving, "atomic_place", failing_once(THIRD_NAME))
    prepared_set, tracks = prepare_tracks(album, settings, history, left_out=(4,))
    left_out = tracks[3].prepared
    assert left_out is not None
    tracks[3].staged = pipeline.fetch(left_out)
    results = pipeline.finalize_set(prepared_set, tracks, settings)
    outcomes = [result.outcome for result in results[:4]]
    assert outcomes == ["saved", "name_taken", "failed", "left_out"]
    assert [staging_of(track).exists() for track in tracks[:4]] == [False, False, True, True]
    assert [track.staged is not None for track in tracks[:4]] == [False, False, True, True]


def test_a_staged_track_isnt_downloaded_again(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set, tracks = prepare_tracks(album, settings, history)
    first = tracks[0].prepared
    assert first is not None
    tracks[0].staged = pipeline.fetch(first)
    downloads_before = len(album.downloaded)
    pipeline.finalize_set(prepared_set, tracks, settings)
    assert len(album.downloaded) == downloads_before + 7


def test_one_track_saves_alone_in_its_place_in_the_album(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set, tracks = prepare_tracks(album, settings, history)
    places = pipeline.album_places(prepared_set, tracks, settings)
    result = pipeline.finalize_set_track(tracks[4], places[4], settings)
    assert (result.outcome, result.track_no, result.track_total) == ("saved", 5, 8)
    assert result.saved is not None
    assert album_tags(result.saved.path) == ("out of spite", "Levi Ryan", "5/8")
    assert cover_side(result.saved.path) == SET_COVER_SIDE
    assert len(album.downloaded) == 1
    assert [track.saved is not None for track in tracks] == [i == 4 for i in range(8)]


def test_one_track_without_a_place_saves_as_a_single(
    album: FakeSet, settings: Settings, history: History
) -> None:
    _, tracks = prepare_tracks(album, settings, history)
    result = pipeline.finalize_set_track(tracks[0], None, settings)
    assert (result.outcome, result.track_no) == ("saved", None)
    assert result.saved is not None
    tags = ID3(result.saved.path)
    assert str(tags["TALB"]) == "crackshot w/gl0wrm"
    assert "TRCK" not in tags


def test_once_every_track_is_saved_no_cover_is_loaded_for_the_album(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set, tracks = prepare_tracks(album, settings, history)
    pipeline.finalize_set(prepared_set, tracks, settings)
    requests = len(album.image_requests)
    places = pipeline.album_places(prepared_set, tracks, settings)
    assert len(album.image_requests) == requests
    assert [place.track_no if place else None for place in places] == list(range(1, 9))
