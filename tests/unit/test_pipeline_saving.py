"""Tests for `finalize` and `destination`: covers, tags, taken names, album folders and history."""

import importlib.util
import logging
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from mutagen.id3 import ID3

from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import ArtworkChoice, Settings
from scmp3.core.pipeline import saving


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
TYPICAL_URL = fakes.TYPICAL_URL
TYPICAL_ID = fakes.TYPICAL_ID
ARTWORK = fakes.ARTWORK
FakeSoundCloud = fakes.FakeSoundCloud
BrokenHistory = fakes.BrokenHistory
prepare = fakes.prepare
save = fakes.save
finalize = fakes.finalize
staging_files = fakes.staging_files
png = fakes.png
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
world = fakes.world


def test_finalize_tags_places_and_records_the_file(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    saved = save(prepared, settings)
    assert saved == settings.library_dir / "80M" / "80M - The Following.mp3"
    tags = ID3(saved)
    assert tags.version == (2, 3, 0)
    assert str(tags["TIT2"]) == "The Following"
    assert str(tags["TPE1"]) == "80M"
    assert str(tags["TXXX:SCMP3_SOURCE_ID"]) == TYPICAL_ID
    assert str(tags["COMM::eng"]) == TYPICAL_URL
    assert tags.getall("APIC")[0].mime == "image/jpeg"
    entry = history.find_by_source_id(TYPICAL_ID)
    assert entry is not None
    assert (entry.saved_path, entry.format, entry.bitrate) == (saved, "mp3", 320)
    # The staging folder goes once the file is in the library.
    assert staging_files() == []


def test_the_cover_falls_back_to_the_500_px_size(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    del world.images[f"{ARTWORK}-original.jpg"]
    saved = save(prepare(world, settings, history), settings)
    assert saved is not None
    assert world.image_requests == [
        f"{ARTWORK}-original.jpg",
        f"{ARTWORK}-original.png",
        f"{ARTWORK}-t500x500.jpg",
    ]
    assert ID3(saved).getall("APIC")


def test_a_cover_that_wont_download_is_left_out_and_reported(
    world: FakeSoundCloud, settings: Settings, history: History, caplog: pytest.LogCaptureFixture
) -> None:
    world.images.clear()
    saved = finalize(prepare(world, settings, history), settings)
    assert saved is not None
    assert saved.cover_missing
    assert saved.cover_error is not None
    assert saved.cover_error.kind == ErrorKind.unavailable
    assert ID3(saved.path).getall("APIC") == []
    assert history.find_by_source_id(TYPICAL_ID) is not None
    assert "Saving without a cover" in caplog.text


def test_a_cover_image_that_cant_be_read_is_left_out_and_reported(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    world.images = {f"{ARTWORK}-original.jpg": b"not an image", f"{ARTWORK}-t500x500.jpg": b"no"}
    saved = finalize(prepare(world, settings, history), settings)
    assert saved is not None
    assert saved.cover_error is not None
    assert saved.cover_error.kind == ErrorKind.convert_failed


def test_a_cover_that_loads_leaves_nothing_missing(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    saved = finalize(prepare(world, settings, history), settings)
    assert saved is not None
    assert not saved.cover_missing


def test_a_bug_in_the_cover_choice_still_fails_the_save(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    with pytest.raises(ScmpError) as raised:
        save(prepared, settings, cover=ArtworkChoice("url"))
    assert raised.value.kind == ErrorKind.internal
    assert not settings.library_dir.exists()


def test_choosing_no_cover_saves_without_one(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    saved = save(prepare(world, settings, history), settings, cover=ArtworkChoice("none"))
    assert saved is not None
    assert ID3(saved).getall("APIC") == []
    assert world.image_requests == []


def test_a_taken_name_gets_a_number_when_keeping_both(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    flat = replace(settings, artist_folders=False)
    taken = flat.library_dir / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"someone else's file")
    saved = save(prepare(world, flat, history), flat)
    assert saved == flat.library_dir / "80M - The Following (2).mp3"
    assert taken.read_bytes() == b"someone else's file"


def test_a_taken_name_in_the_artists_folder_gets_a_number(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    taken = settings.library_dir / "80M" / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"someone else's file")
    saved = save(prepare(world, settings, history), settings)
    assert saved == settings.library_dir / "80M" / "80M - The Following (2).mp3"
    assert taken.read_bytes() == b"someone else's file"


def test_a_save_reuses_an_artist_folder_that_differs_only_by_case(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    (settings.library_dir / "80m").mkdir(parents=True)
    saved = save(prepare(world, settings, history), settings)
    assert saved is not None
    # Names are compared as text, since Windows paths are equal ignoring case.
    assert saved.parent.name == "80m"
    assert [path.name for path in settings.library_dir.iterdir()] == ["80m"]


def test_the_folder_is_reused_before_the_taken_name_check(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    taken = settings.library_dir / "80m" / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"old")
    skip = replace(settings, when_file_exists="skip")
    assert save(prepare(world, skip, history), skip) is None
    assert taken.read_bytes() == b"old"


def test_skipping_a_taken_name_saves_nothing_and_clears_the_staging_folder(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    taken = settings.library_dir / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"someone else's file")
    skip = replace(settings, when_file_exists="skip", artist_folders=False)
    assert save(prepare(world, skip, history), skip) is None
    assert taken.read_bytes() == b"someone else's file"
    assert history.find_by_source_id(TYPICAL_ID) is None
    assert staging_files() == []


def test_the_ask_policy_asks_about_a_taken_name(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    taken = settings.library_dir / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"old")
    asked: list[Path] = []

    def ask(path: Path) -> Any:
        asked.append(path)
        return "replace"

    ask_settings = replace(settings, when_file_exists="ask", artist_folders=False)
    saved = save(prepare(world, ask_settings, history), ask_settings, ask=ask)
    assert asked == [taken]
    assert saved == taken
    assert taken.read_bytes() != b"old"


def test_what_ask_raises_reaches_the_caller_unchanged(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    class InputClosed(Exception):
        pass

    def ask(path: Path) -> Any:
        raise InputClosed

    taken = settings.library_dir / "80M - The Following.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"old")
    ask_settings = replace(settings, when_file_exists="ask", artist_folders=False)
    with pytest.raises(InputClosed):
        save(prepare(world, ask_settings, history), ask_settings, ask=ask)
    assert taken.read_bytes() == b"old"
    assert history.find_by_source_id(TYPICAL_ID) is None


def test_a_disk_error_while_placing_the_file_is_about_the_library(
    world: FakeSoundCloud, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    def read_only(src: Path, dest: Path) -> Path:
        raise ScmpError(ErrorKind.disk, f"can't write {dest.name}: Access is denied")

    monkeypatch.setattr(saving, "atomic_place", read_only)
    with pytest.raises(ScmpError) as raised:
        save(prepare(world, settings, history), settings)
    assert (raised.value.kind, raised.value.where) == (ErrorKind.disk, "library")


def test_a_place_a_step_already_gave_is_kept(
    world: FakeSoundCloud, settings: Settings, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    def data_full(src: Path, dest: Path) -> Path:
        raise ScmpError(ErrorKind.disk, "the data drive is full", where="data")

    monkeypatch.setattr(saving, "atomic_place", data_full)
    with pytest.raises(ScmpError) as raised:
        save(prepare(world, settings, history), settings)
    assert raised.value.where == "data"


def test_a_history_failure_after_the_save_only_warns(
    world: FakeSoundCloud, settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    prepared = prepare(world, settings, BrokenHistory(tmp_path / "history.db"))
    saved = save(prepared, settings)
    assert saved is not None
    assert saved.is_file()
    assert "wasn't added to the history" in caplog.text


def test_the_title_style_picks_the_file_name(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    staged = pipeline.fetch(prepared)
    source, suggestion = prepared.source, prepared.suggestion
    tags = pipeline.render_tags(source, suggestion, settings)
    plain = pipeline.destination(source, suggestion, staged, tags, settings)
    styled = pipeline.destination(source, suggestion, staged, tags, settings, style="all_in_title")
    assert plain.name == "80M - The Following.mp3"
    assert styled.name == "The Following.mp3"


@pytest.mark.parametrize(
    ("publisher_album", "overrides", "folder"),
    [
        # A single's album comes from the album-for-singles setting, so it stays at the top.
        (None, {}, ""),
        (None, {"album": "The Following"}, ""),
        ("Night Drives EP", {}, "80M - Night Drives EP"),
        (None, {"album": "Night Drives EP"}, "80M - Night Drives EP"),
        # The source's own title still counts after the title is edited.
        ("The Following", {"title": "Following"}, ""),
    ],
)
def test_album_subfolders_are_only_for_albums_that_arent_the_track_itself(
    publisher_album: str | None,
    overrides: dict[str, str],
    folder: str,
    world: FakeSoundCloud,
    settings: Settings,
    history: History,
) -> None:
    world.fixture["api"]["publisher_metadata"]["album_title"] = publisher_album
    subfolders = replace(settings, album_subfolders=True, artist_folders=False)
    saved = save_with_overrides(prepare(world, subfolders, history), subfolders, overrides)
    assert saved.parent == subfolders.library_dir / folder


def test_a_fixed_album_for_singles_keeps_singles_at_the_top(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    fixed = replace(
        settings,
        album_subfolders=True,
        artist_folders=False,
        singles_album="fixed",
        singles_album_text="SC singles",
    )
    saved = save_with_overrides(prepare(world, fixed, history), fixed, {})
    assert ID3(saved)["TALB"].text == ["SC singles"]
    assert saved.parent == fixed.library_dir


def save_with_overrides(
    prepared: pipeline.Prepared, settings: Settings, overrides: dict[str, str]
) -> Path:
    """Fetch and finalize with hand edits and no cover."""
    staged = pipeline.fetch(prepared)
    tags = pipeline.render_tags(prepared.source, prepared.suggestion, settings, overrides=overrides)
    no_cover = ArtworkChoice("none")
    saved = pipeline.finalize(prepared, staged, tags, no_cover, settings, overrides=overrides)
    assert saved is not None
    return saved.path


def test_a_typed_artist_saves_in_their_own_folder(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    # A repost channel's upload, where the artist falls back to the uploader.
    info, api = world.fixture["info"], world.fixture["api"]
    info.update(title="Midnight Drive", uploader="Chill Nation", artist=None, artists=None)
    api["publisher_metadata"]["artist"] = None
    api["user"]["username"] = "Chill Nation"
    prepared = prepare(world, settings, history)
    assert prepared.suggestion.main_artists == ["Chill Nation"]
    saved = save_with_overrides(prepared, settings, {"artist": "Mara Lune"})
    assert saved == settings.library_dir / "Mara Lune" / "Mara Lune - Midnight Drive.mp3"
    tags = ID3(saved)
    assert (tags["TPE1"].text, tags["TPE2"].text) == (["Mara Lune"], ["Mara Lune"])


@pytest.mark.parametrize("typed", ["80M, Lou Vee", "80M, Lou V, Mara"])
def test_editing_a_feature_or_adding_a_name_keeps_the_artists_folder(
    typed: str, world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    world.fixture["info"]["title"] = "The Following (feat. Lou V)"
    prepared = prepare(world, settings, history)
    assert prepared.suggestion.featured == ["Lou V"]
    saved = save_with_overrides(prepared, settings, {"artist": typed})
    assert saved == settings.library_dir / "80M" / f"{typed} - The Following.mp3"
    assert ID3(saved)["TPE2"].text == ["80M"]


def test_an_album_that_is_only_the_base_title_counts_as_a_single(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    # The base title, "The Following", matches neither the raw title nor the edited one.
    world.fixture["info"]["title"] = "80M - The Following FREE DL"
    world.fixture["api"]["publisher_metadata"]["album_title"] = "The Following"
    subfolders = replace(settings, album_subfolders=True, artist_folders=False)
    prepared = prepare(world, subfolders, history)
    assert prepared.suggestion.base_title == "The Following"
    saved = save_with_overrides(prepared, subfolders, {"title": "Following"})
    assert saved.parent == subfolders.library_dir


def test_a_track_saved_from_an_album_set_gets_the_album_folder_whatever_its_tags(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    # Alone, "The Following" on the album "The Following" counts as a single and stays flat.
    subfolders = replace(settings, album_subfolders=True)
    prepared = prepare(world, subfolders, history)
    staged = pipeline.fetch(prepared)
    edits = {"album": "The Following", "track_no": "1"}
    tags = pipeline.render_tags(prepared.source, prepared.suggestion, subfolders, overrides=edits)
    suggestion = prepared.suggestion
    planned = pipeline.destination(
        prepared.source, suggestion, staged, tags, subfolders, overrides=edits, in_album_set=True
    )
    no_cover = ArtworkChoice("none")
    saved = pipeline.finalize(
        prepared, staged, tags, no_cover, subfolders, overrides=edits, in_album_set=True
    )
    assert saved is not None
    folder = subfolders.library_dir / "80M" / "The Following"
    assert saved.path == planned == folder / "01 80M - The Following.mp3"
