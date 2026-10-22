"""Tests for saving sets: listing and resolving them, then the album's fields, numbers and folders,
and saving a set as singles. Covers and failures have test files of their own."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from mutagen.id3 import ID3

from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import Settings
from scmp3.core.sources import soundcloud_sets


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
ALBUM_URL = set_fakes.ALBUM_URL
PLAYLIST_URL = set_fakes.PLAYLIST_URL
TRACK_COVER_SIDE = set_fakes.TRACK_COVER_SIDE
prepare_set = set_fakes.prepare_set
prepare_tracks = set_fakes.prepare_tracks
save_set = set_fakes.save_set
saved_paths = set_fakes.saved_paths
album_tags = set_fakes.album_tags
with_artists = set_fakes.with_artists
cover_side = set_fakes.cover_side
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
album = set_fakes.album

ALBUM_TITLES = [
    "crackshot w-gl0wrm",
    "matrix w-david shawty",
    "silkroad w-LONEWOLFPRIE",
    "purextc",
    "bright eyes w-funeral",
    "Track 6",
    "Track 7",
    "Track 8",
]


def test_prepare_set_lists_the_album_and_picks_album_mode(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set = prepare_set(album, settings, history)
    assert prepared_set.url == ALBUM_URL
    assert prepared_set.default_mode == "album"
    assert prepared_set.playlist.title == "out of spite"
    assert len(prepared_set.playlist.entries) == 8
    # Listing extracts the set alone, and quickly, with no track resolved yet.
    assert album.extracted == [ALBUM_URL]
    assert album.options == [soundcloud_sets.LISTING_OPTIONS]


def test_a_plain_playlist_defaults_to_singles(
    tiny_m4a: Path, settings: Settings, history: History
) -> None:
    world = set_fakes.make_set(tiny_m4a, "playlist")
    assert prepare_set(world, settings, history, PLAYLIST_URL).default_mode == "singles"


def test_prepare_set_refuses_a_set_with_no_tracks(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.fixture["info"]["entries"] = []
    with pytest.raises(ScmpError) as raised:
        prepare_set(album, settings, history)
    assert raised.value.kind == ErrorKind.unavailable


def test_prepare_set_takes_only_set_links(settings: Settings) -> None:
    with pytest.raises(ValueError, match="soundcloud_track"):
        pipeline.prepare_set(classify(fakes.TYPICAL_URL), settings=settings)


def test_each_track_resolves_on_its_own_like_a_link(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set = prepare_set(album, settings, history)
    entry = prepared_set.playlist.entries[6]
    prepared = pipeline.prepare_set_track(prepared_set, entry, job_id="track-7")
    assert prepared.job_id == "track-7"
    assert prepared.source.id == entry.id == "soundcloud:tracks:1378973569"
    assert prepared.source.title == "Track 7"
    # The listing gives this track's api link, and the source keeps its page instead.
    assert entry.url == "https://api.soundcloud.com/tracks/1378973569"
    assert prepared.source.url == "https://soundcloud.com/leviryan/track-1378973569"
    assert prepared.suggestion.main_artists == ["Levi Ryan"]
    # Each track is extracted alone, after the listing.
    assert album.extracted == [ALBUM_URL, entry.url]


def test_a_track_that_cant_be_resolved_keeps_its_error_and_the_rest_resolve(
    album: FakeSet, settings: Settings, history: History
) -> None:
    set_fakes.unresolvable(album, 3)
    _, tracks = prepare_tracks(album, settings, history)
    assert [track.prepared is None for track in tracks] == [i == 2 for i in range(8)]
    assert tracks[2].error is not None
    assert tracks[2].error.kind == ErrorKind.unavailable


def test_an_album_saves_every_track_in_order_with_shared_album_tags(
    album: FakeSet, settings: Settings, history: History
) -> None:
    _, results = save_set(album, settings, history)
    assert [result.outcome for result in results] == ["saved"] * 8
    assert [(result.track_no, result.track_total) for result in results] == [
        (number, 8) for number in range(1, 9)
    ]
    folder = settings.library_dir / "Levi Ryan"
    assert saved_paths(results) == [folder / f"Levi Ryan - {title}.mp3" for title in ALBUM_TITLES]
    assert [album_tags(path) for path in saved_paths(results)] == [
        ("out of spite", "Levi Ryan", f"{number}/8") for number in range(1, 9)
    ]


def test_an_album_shares_the_sets_year_and_genre(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.track(2)["info"]["genres"] = ["Drum & Bass"]
    _, results = save_set(album, settings, history)
    tags = [ID3(path) for path in saved_paths(results)]
    assert {str(tag["TDRC"]) for tag in tags} == {"2022"}
    assert {str(tag["TCON"]) for tag in tags} == {"Hip-hop & Rap"}


def test_without_a_year_or_genre_of_its_own_the_album_shares_its_first_tracks(
    album: FakeSet, settings: Settings, history: History
) -> None:
    album.fixture["api"].update(release_date=None, genre="")
    album.fixture["info"].update(release_date=None, genres=[])
    album.track(2)["info"]["genres"] = ["Drum & Bass"]
    _, results = save_set(album, settings, history)
    tags = [ID3(path) for path in saved_paths(results)]
    # The typical track the fake tracks are made from came out in 2017.
    assert {str(tag["TDRC"]) for tag in tags} == {"2017"}
    assert {str(tag["TCON"]) for tag in tags} == {"Dance & EDM"}


@pytest.mark.parametrize(
    ("artist_folders", "album_subfolders", "folder", "numbered"),
    [
        (True, True, ("Levi Ryan", "out of spite"), True),
        (False, True, ("Levi Ryan - out of spite",), True),
        (True, False, ("Levi Ryan",), False),
        (False, False, (), False),
    ],
)
def test_an_album_goes_in_the_folders_the_settings_ask_for(
    artist_folders: bool,
    album_subfolders: bool,
    folder: tuple[str, ...],
    numbered: bool,
    album: FakeSet,
    settings: Settings,
    history: History,
) -> None:
    settings = replace(settings, artist_folders=artist_folders, album_subfolders=album_subfolders)
    _, results = save_set(album, settings, history)
    # Only an album folder puts the number in front of the name.
    names = [f"Levi Ryan - {title}.mp3" for title in ALBUM_TITLES]
    if numbered:
        names = [f"{number:02} {name}" for number, name in enumerate(names, start=1)]
    where = settings.library_dir.joinpath(*folder)
    assert saved_paths(results) == [where / name for name in names]
    # The tags hold the number either way.
    assert [album_tags(path)[2] for path in saved_paths(results)] == [
        f"{number}/8" for number in range(1, 9)
    ]


def test_left_out_tracks_arent_saved_and_the_rest_are_numbered_without_them(
    album: FakeSet, settings: Settings, history: History
) -> None:
    _, results = save_set(album, settings, history, left_out=(2, 5))
    outcomes = [result.outcome for result in results]
    assert outcomes == [
        "saved",
        "left_out",
        "saved",
        "saved",
        "left_out",
        "saved",
        "saved",
        "saved",
    ]
    assert [album_tags(path)[2] for path in saved_paths(results)] == [
        f"{number}/6" for number in range(1, 7)
    ]
    assert [result.track_no for result in results] == [1, None, 2, 3, None, 4, 5, 6]


def test_a_typed_artist_keeps_the_track_in_the_album(
    album: FakeSet, settings: Settings, history: History
) -> None:
    settings = replace(settings, album_subfolders=True)
    prepared_set, tracks = prepare_tracks(album, settings, history)
    tracks[0].overrides = {"artist": "gl0wrm"}
    results = pipeline.finalize_set(prepared_set, tracks, settings)
    first = saved_paths(results)[0]
    assert first.parent == settings.library_dir / "Levi Ryan" / "out of spite"
    assert str(ID3(first)["TPE1"]) == "gl0wrm"
    assert album_tags(first) == ("out of spite", "Levi Ryan", "1/8")


def test_edited_album_fields_apply_to_every_track(
    album: FakeSet, settings: Settings, history: History
) -> None:
    edited = {"album": "Out Of Spite (Deluxe)", "album_artist": "Levi Ryan"}
    _, results = save_set(album, settings, history, album=edited)
    assert {album_tags(path)[0] for path in saved_paths(results)} == {"Out Of Spite (Deluxe)"}


def test_an_edit_to_some_album_fields_keeps_the_shared_others(
    album: FakeSet, settings: Settings, history: History
) -> None:
    settings = replace(settings, album_subfolders=True)
    prepared_set, tracks = prepare_tracks(album, settings, history)
    # A typed artist would take a track's album artist with it, if the album didn't share one.
    tracks[0].overrides = {"artist": "gl0wrm"}
    results = pipeline.finalize_set(prepared_set, tracks, settings, album={"album": "Spite"})
    paths = saved_paths(results)
    assert {path.parent for path in paths} == {settings.library_dir / "Levi Ryan" / "Spite"}
    assert {album_tags(path)[:2] for path in paths} == {("Spite", "Levi Ryan")}


def test_an_album_whose_tracks_share_one_artist_files_under_that_artist(
    album: FakeSet, settings: Settings, history: History
) -> None:
    # As when a label uploads one artist's album.
    with_artists(album, {position: "Mara Lune" for position in range(1, 9)})
    _, results = save_set(album, settings, history)
    paths = saved_paths(results)
    assert {album_tags(path)[1] for path in paths} == {"Mara Lune"}
    assert {path.parent for path in paths} == {settings.library_dir / "Mara Lune"}


def test_an_album_of_several_artists_files_under_the_sets_owner(
    album: FakeSet, settings: Settings, history: History
) -> None:
    # As with a label's sampler.
    with_artists(album, {n: "Mara Lune" if n % 2 else "Teo Marr" for n in range(1, 9)})
    _, results = save_set(album, settings, history)
    paths = saved_paths(results)
    assert {album_tags(path)[1] for path in paths} == {"Levi Ryan"}
    assert {path.parent for path in paths} == {settings.library_dir / "Levi Ryan"}


def test_a_typed_album_artist_wins_over_the_one_the_tracks_share(
    album: FakeSet, settings: Settings, history: History
) -> None:
    with_artists(album, {position: "Mara Lune" for position in range(1, 9)})
    _, results = save_set(album, settings, history, album={"album_artist": "Various Artists"})
    assert {album_tags(path)[1] for path in saved_paths(results)} == {"Various Artists"}


def test_a_track_titled_like_its_album_still_gets_the_album_folder(
    album: FakeSet, settings: Settings, history: History
) -> None:
    # Alone, a track whose album is its own title counts as a single and stays out of folders.
    album.track(4)["info"]["title"] = "out of spite"
    settings = replace(settings, album_subfolders=True)
    _, results = save_set(album, settings, history)
    assert saved_paths(results)[3] == (
        settings.library_dir / "Levi Ryan" / "out of spite" / "04 Levi Ryan - out of spite.mp3"
    )


def test_singles_mode_saves_each_track_on_its_own(
    album: FakeSet, settings: Settings, history: History
) -> None:
    settings = replace(settings, album_subfolders=True)
    _, results = save_set(album, settings, history, mode="singles")
    paths = saved_paths(results)
    # Singles stay flat in their artist's folder, with no number, as a single link saves.
    assert paths == [
        settings.library_dir / "Levi Ryan" / f"Levi Ryan - {title}.mp3" for title in ALBUM_TITLES
    ]
    tags = ID3(paths[0])
    assert str(tags["TALB"]) == "crackshot w/gl0wrm"
    assert "TRCK" not in tags
    assert {result.track_no for result in results} == {None}
    # Each single keeps its own cover.
    assert [cover_side(path) for path in paths] == [TRACK_COVER_SIDE + n for n in range(1, 9)]


def test_a_plain_playlist_saves_as_singles_by_default(
    tiny_m4a: Path, settings: Settings, history: History
) -> None:
    world = set_fakes.make_set(tiny_m4a, "playlist")
    prepared_set = prepare_set(world, settings, history, PLAYLIST_URL)
    results = pipeline.finalize_set(
        prepared_set, pipeline.prepare_set_tracks(prepared_set), settings
    )
    assert [result.outcome for result in results] == ["saved"] * 6
    assert all("TRCK" not in ID3(path) for path in saved_paths(results))


def test_every_saved_track_goes_in_the_history(
    album: FakeSet, settings: Settings, history: History
) -> None:
    prepared_set, results = save_set(album, settings, history)
    entries = prepared_set.playlist.entries
    for entry, path in zip(entries, saved_paths(results), strict=True):
        assert entry.id is not None
        found = history.find_by_source_id(entry.id)
        assert found is not None
        assert found.saved_path == path
