"""Tests for library filenames, destinations and collisions."""

import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scmp3.core import library
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import (
    plan_destination,
    render_filename,
    resolve_collision,
    reuse_folders_by_case,
)
from scmp3.core.models import CollisionPolicy, Settings, SourceInfo, Tags, TitleStyle
from scmp3.core.suggest import render, suggest

SAMPLE = Tags(
    title="Midnight Drive (feat. Lou V)",
    artist="Kaia",
    album="Night Shift",
    album_artist="Kaia",
    year=2026,
    track_no=3,
    track_total=12,
)
SAMPLE_NAME = "Kaia - Midnight Drive (feat. Lou V)"

# Built with chr() so the exact code points are plain to see.
COMBINING_ACUTE = chr(0x301)
E_ACUTE = chr(0xE9)
# A CJK character: three UTF-8 bytes.
NIGHT = chr(0x591C)


@pytest.fixture(autouse=True)
def no_path_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave out Windows' MAX_PATH, so the temp folder's length can't change any name."""
    monkeypatch.setattr(library, "MAX_PATH_UNITS", None)


def flat(**changes: Any) -> Settings:
    """Settings with artist folders off, so only the album rule can add a folder."""
    return Settings(artist_folders=False, **changes)


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def planned_and_reused(library_dir: Path, tags: Tags, settings: Settings) -> Path:
    """Where a save goes: the planned path, then any folder already there that differs by case."""
    planned = plan_destination(library_dir, tags, settings, "mp3")
    return reuse_folders_by_case(planned, library_dir)


def pretend_taken(monkeypatch: pytest.MonkeyPatch, *taken: Path) -> None:
    """Make exists() true for these paths only, for names too long to create here."""
    monkeypatch.setattr(Path, "exists", lambda path, *args, **kwargs: path in taken)


def fail_on_purpose(message: str) -> Callable[..., None]:
    """A stand-in that fails the test if it's ever called."""

    def stand_in(*args: object) -> None:
        pytest.fail(message)

    return stand_in


def test_render_filename_fills_the_default_template() -> None:
    assert render_filename("{artist} - {title}", SAMPLE) == SAMPLE_NAME


def test_render_filename_understands_every_token() -> None:
    template = "{track:02} {track} {album_artist} - {album} ({year}) {artist} - {title}"
    assert render_filename(template, SAMPLE) == f"03 3 Kaia - Night Shift (2026) {SAMPLE_NAME}"


@pytest.mark.parametrize(("track_no", "expected"), [(1, "01"), (12, "12"), (123, "123")])
def test_render_filename_pads_the_track_to_two_digits(track_no: int, expected: str) -> None:
    tags = dataclasses.replace(SAMPLE, track_no=track_no)
    assert render_filename("{track:02}", tags) == expected


def test_render_filename_drops_empty_tokens_and_their_brackets() -> None:
    tags = dataclasses.replace(SAMPLE, year=None, track_no=None)
    assert render_filename("{track:02} {artist} - {title} ({year}) [{track}]", tags) == SAMPLE_NAME


def test_render_filename_drops_the_dash_an_empty_artist_leaves() -> None:
    tags = dataclasses.replace(SAMPLE, artist="")
    assert render_filename("{artist} - {title}", tags) == "Midnight Drive (feat. Lou V)"


def test_a_slash_in_a_value_cant_make_a_subfolder() -> None:
    tags = dataclasses.replace(SAMPLE, artist="AC/DC", title="Back\\In Black")
    assert render_filename("{artist} - {title}", tags) == "AC-DC - Back-In Black"


def test_render_filename_sanitizes_the_template_text_too() -> None:
    assert render_filename("{artist}: {title}?", SAMPLE) == "Kaia- Midnight Drive (feat. Lou V)"


def test_render_filename_leaves_unknown_tokens_as_written() -> None:
    assert render_filename("{artist} - {title} {genre}", SAMPLE) == f"{SAMPLE_NAME} {{genre}}"


def test_render_filename_doesnt_fill_in_braces_inside_values() -> None:
    tags = dataclasses.replace(SAMPLE, title="{artist}")
    assert render_filename("{artist} - {title}", tags) == "Kaia - {artist}"


def test_by_default_a_track_is_artist_dash_title_in_its_artists_folder(tmp_path: Path) -> None:
    destination = plan_destination(tmp_path, SAMPLE, Settings(), "mp3")
    assert destination == tmp_path / "Kaia" / f"{SAMPLE_NAME}.mp3"


@pytest.mark.parametrize("style", ["artist_field", "feat_in_title", "custom"])
def test_every_style_but_all_in_title_defaults_to_artist_dash_title(
    style: TitleStyle, tmp_path: Path
) -> None:
    settings = Settings(title_style=style)
    assert plan_destination(tmp_path, SAMPLE, settings, "mp3").name == f"{SAMPLE_NAME}.mp3"


def test_plan_destination_uses_the_title_alone_under_all_in_title(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive (Kaia, Lou V)")
    settings = Settings(title_style="all_in_title")
    destination = plan_destination(tmp_path, tags, settings, "mp3")
    assert destination.name == "Midnight Drive (Kaia, Lou V).mp3"


def test_a_tracks_own_style_picks_the_default_template(tmp_path: Path) -> None:
    destination = plan_destination(tmp_path, SAMPLE, Settings(), "mp3", style="all_in_title")
    assert destination.name == "Midnight Drive (feat. Lou V).mp3"


def test_a_filename_template_in_the_settings_beats_the_style_default(tmp_path: Path) -> None:
    settings = Settings(filename_template="{title} by {artist}", title_style="all_in_title")
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3")
    assert destination.name == "Midnight Drive (feat. Lou V) by Kaia.mp3"


@pytest.mark.parametrize("template", ["", "   "])
def test_a_blank_filename_template_means_the_default(template: str, tmp_path: Path) -> None:
    settings = Settings(filename_template=template)
    assert plan_destination(tmp_path, SAMPLE, settings, "mp3").name == f"{SAMPLE_NAME}.mp3"


@pytest.mark.parametrize("ext", ["m4a", ".m4a"])
def test_plan_destination_takes_the_extension_with_or_without_a_dot(
    ext: str, tmp_path: Path
) -> None:
    assert plan_destination(tmp_path, SAMPLE, Settings(), ext).name == f"{SAMPLE_NAME}.m4a"


def test_plan_destination_caps_the_whole_name_with_its_extension(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, title="a" * 200)
    destination = plan_destination(tmp_path, tags, Settings(title_style="all_in_title"), "mp3")
    assert destination.name == "a" * 146 + ".mp3"


def test_album_subfolders_add_an_album_folder_and_a_track_number(tmp_path: Path) -> None:
    settings = flat(album_subfolders=True)
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3")
    assert destination == tmp_path / "Kaia - Night Shift" / f"03 {SAMPLE_NAME}.mp3"


def test_album_subfolders_leave_out_a_missing_track_number(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, track_no=None)
    destination = plan_destination(tmp_path, tags, flat(album_subfolders=True), "mp3")
    assert destination == tmp_path / "Kaia - Night Shift" / f"{SAMPLE_NAME}.mp3"


def test_album_subfolders_dont_repeat_a_track_number_the_template_has(tmp_path: Path) -> None:
    settings = Settings(album_subfolders=True, filename_template="{track} {title}")
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3")
    assert destination.name == "3 Midnight Drive (feat. Lou V).mp3"


def test_a_track_with_no_album_stays_at_the_top_of_the_library(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album=" ")
    destination = plan_destination(tmp_path, tags, flat(album_subfolders=True), "mp3")
    assert destination == tmp_path / f"{SAMPLE_NAME}.mp3"


@pytest.mark.parametrize(
    ("title", "album"),
    [
        ("Midnight Drive", "Midnight Drive"),
        ("Midnight Drive (feat. Lou V)", "Midnight Drive"),
        ("Midnight Drive (Kaia, Lou V)", "Midnight Drive"),
        ("Midnight Drive [ft. Lou V]", "Midnight Drive"),
        ("Midnight Drive (feat. Lou V) (VIP Mix)", "Midnight Drive (VIP Mix)"),
        ("Midnight Drive [prod. Hazy] (feat. Lou V)", "Midnight Drive [prod. Hazy]"),
        ("midnight  drive", "Midnight Drive"),
        # The source's album keeps what the cleanup took out of the title.
        ("Midnight Drive", "Midnight Drive (feat. Lou V)"),
        ("Midnight Drive", "Midnight Drive [FREE DL]"),
    ],
)
def test_a_single_stays_at_the_top_with_album_subfolders_on(
    tmp_path: Path, title: str, album: str
) -> None:
    tags = dataclasses.replace(SAMPLE, title=title, album=album, track_no=None)
    destination = plan_destination(tmp_path, tags, flat(album_subfolders=True), "mp3")
    assert destination.parent == tmp_path


@pytest.mark.parametrize(
    "title", ["Midnight Drive ft. Lou V", "Kaia, Lou V - Midnight Drive", "Late Night Edit"]
)
def test_an_album_from_the_singles_setting_means_a_single_whatever_the_title(
    tmp_path: Path, title: str
) -> None:
    tags = dataclasses.replace(SAMPLE, title=title, album="Midnight Drive", track_no=None)
    settings = flat(album_subfolders=True)
    destination = plan_destination(tmp_path, tags, settings, "mp3", album_from_singles_setting=True)
    assert destination.parent == tmp_path


def test_the_base_and_raw_titles_count_as_the_tracks_own(tmp_path: Path) -> None:
    # A custom template that adds the artists without brackets, with the album from the source.
    tags = dataclasses.replace(
        SAMPLE, title="Kaia, Lou V - Midnight Drive", album="Midnight Drive [FREE DL]"
    )
    settings = flat(album_subfolders=True)
    titles = ("Midnight Drive", "Midnight Drive (feat. Lou V) [FREE DL]")
    destination = plan_destination(tmp_path, tags, settings, "mp3", source_titles=titles)
    assert destination.parent == tmp_path


def test_an_album_named_after_none_of_the_titles_gets_its_folder(tmp_path: Path) -> None:
    titles = ("Midnight Drive", "Midnight Drive (feat. Lou V)")
    settings = flat(album_subfolders=True)
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3", source_titles=titles)
    assert destination == tmp_path / "Kaia - Night Shift" / f"03 {SAMPLE_NAME}.mp3"


def test_a_title_track_from_an_album_set_goes_in_the_albums_folder(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, title="Lantern", album="Lantern", track_no=1)
    settings = flat(album_subfolders=True)
    destination = plan_destination(tmp_path, tags, settings, "mp3", in_album_set=True)
    assert destination == tmp_path / "Kaia - Lantern" / "01 Kaia - Lantern.mp3"
    # Without the set, nothing tells it apart from a single, and a single stays at the top.
    assert plan_destination(tmp_path, tags, settings, "mp3").parent == tmp_path


def test_a_set_in_album_mode_wins_over_the_singles_setting() -> None:
    assert library.is_album_track(SAMPLE, in_album_set=True, album_from_singles_setting=True)


def test_a_track_with_no_album_isnt_an_album_track_even_from_a_set() -> None:
    tags = dataclasses.replace(SAMPLE, album="")
    assert not library.is_album_track(tags, in_album_set=True)


def test_album_subfolders_off_keeps_album_tracks_at_the_top(tmp_path: Path) -> None:
    destination = plan_destination(tmp_path, SAMPLE, flat(), "mp3", in_album_set=True)
    assert destination == tmp_path / f"{SAMPLE_NAME}.mp3"


@pytest.mark.parametrize(
    ("raw_title", "album", "template"),
    [
        ("Midnight Drive (feat. Lou V)", "Midnight Drive (feat. Lou V)", None),
        ("Midnight Drive [FREE DL]", "Midnight Drive [FREE DL]", None),
        ("Midnight Drive (feat. Lou V)", None, "{title} ft. {featured}{version}"),
        ("Midnight Drive (feat. Lou V)", None, "{artists} - {title}{version}"),
    ],
)
def test_singles_stay_at_the_top_through_suggest_and_render(
    tmp_path: Path, raw_title: str, album: str | None, template: str | None
) -> None:
    style: TitleStyle = "custom" if template else "artist_field"
    settings = flat(
        album_subfolders=True,
        library_dir=tmp_path,
        title_style=style,
        custom_title_template=template or "{title}{version}",
    )
    source = SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title=raw_title,
        uploader="Kaia",
        album=album,
    )
    suggestion = suggest(source, settings)
    tags = render(suggestion, None, {}, settings)
    # What a caller passes: where the album came from, and the titles before the style.
    destination = plan_destination(
        tmp_path,
        tags,
        settings,
        "mp3",
        album_from_singles_setting=suggestion.album is None,
        source_titles=(suggestion.base_title, source.title),
    )
    assert destination.parent == tmp_path


def test_the_album_folder_name_is_sanitized(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album="Night/Shift: Vol. 2...", album_artist="")
    destination = plan_destination(tmp_path, tags, flat(album_subfolders=True), "mp3")
    assert destination.parent == tmp_path / "Night-Shift- Vol. 2"


def test_a_title_shaped_like_a_short_name_cant_reach_another_file(tmp_path: Path) -> None:
    # On NTFS with short names on, KAIASO~1.mp3 is another name for this file.
    make_file(tmp_path / "Kaia Something Long.mp3")
    tags = dataclasses.replace(SAMPLE, title="KAIASO~1")
    destination = plan_destination(tmp_path, tags, flat(title_style="all_in_title"), "mp3")
    assert destination.name == "KAIASO~1_.mp3"
    assert resolve_collision(destination, "skip") == destination


def test_tracks_by_several_artists_go_in_a_folder_each(tmp_path: Path) -> None:
    saved = []
    for artist, title in [("Kaia", "Night Shift"), ("Lou V", "Tides"), ("Kaia", "Lantern")]:
        tags = dataclasses.replace(SAMPLE, title=title, artist=artist, album_artist=artist)
        saved.append(make_file(plan_destination(tmp_path, tags, Settings(), "mp3")))
    assert [path.relative_to(tmp_path).as_posix() for path in saved] == [
        "Kaia/Kaia - Night Shift.mp3",
        "Lou V/Lou V - Tides.mp3",
        "Kaia/Kaia - Lantern.mp3",
    ]


def test_a_single_by_two_artists_goes_in_its_first_main_artists_folder(tmp_path: Path) -> None:
    settings = Settings(library_dir=tmp_path)
    source = SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title="Midnight Drive (feat. Lou V)",
        uploader="Kaia",
    )
    tags = render(suggest(source, settings), None, {}, settings)
    destination = plan_destination(tmp_path, tags, settings, "mp3")
    assert destination == tmp_path / "Kaia" / "Kaia, Lou V - Midnight Drive.mp3"


@pytest.mark.parametrize("album_artist", ["", "   ", "...", "???", " - "])
def test_a_track_without_an_album_artist_goes_in_unknown_artist(
    tmp_path: Path, album_artist: str
) -> None:
    tags = dataclasses.replace(SAMPLE, album_artist=album_artist)
    destination = plan_destination(tmp_path, tags, Settings(), "mp3")
    assert destination == tmp_path / "Unknown artist" / f"{SAMPLE_NAME}.mp3"


@pytest.mark.parametrize(
    ("album_artist", "folder"),
    [
        ("AC/DC", "AC-DC"),
        ("Kaia...", "Kaia"),
        ("Kaia. ", "Kaia"),
        (".Kaia", "Kaia"),
        ("Lou   V", "Lou V"),
        ("CON", "CON_"),
        ("nul", "nul_"),
        ("KAIASO~1", "KAIASO~1_"),
        (f"Beyonce{COMBINING_ACUTE}", f"Beyonc{E_ACUTE}"),
        ("{title}", "{title}"),
    ],
)
def test_the_artist_folder_follows_the_file_name_rules(
    tmp_path: Path, album_artist: str, folder: str
) -> None:
    tags = dataclasses.replace(SAMPLE, album_artist=album_artist)
    assert plan_destination(tmp_path, tags, Settings(), "mp3").parent.name == folder


def test_a_long_artist_name_is_cut_to_100_characters(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album_artist="a" * 150)
    assert plan_destination(tmp_path, tags, Settings(), "mp3").parent.name == "a" * 100


def test_the_cut_leaves_no_dot_or_space_at_the_end_of_the_artist_folder(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album_artist="a" * 98 + ". Lou V")
    assert plan_destination(tmp_path, tags, Settings(), "mp3").parent.name == "a" * 98


def test_a_long_artist_name_in_wide_characters_is_cut_to_240_bytes(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album_artist=NIGHT * 100)
    assert plan_destination(tmp_path, tags, Settings(), "mp3").parent.name == NIGHT * 80


def test_planning_never_lists_the_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "KAIA").mkdir()
    monkeypatch.setattr(library.os, "scandir", fail_on_purpose("plan_destination read the disk"))
    destination = plan_destination(tmp_path, SAMPLE, Settings(album_subfolders=True), "mp3")
    # Names are compared as text, since Windows paths are equal ignoring case.
    assert destination.parent.parent.name == "Kaia"


def test_an_artist_folder_that_differs_only_by_case_is_reused(tmp_path: Path) -> None:
    (tmp_path / "Kaia").mkdir()
    tags = dataclasses.replace(SAMPLE, album_artist="KAIA")
    destination = make_file(planned_and_reused(tmp_path, tags, Settings()))
    assert destination.parent.name == "Kaia"
    assert [path.name for path in tmp_path.iterdir()] == ["Kaia"]


def test_reuse_leaves_a_file_at_the_top_of_the_library_as_planned(tmp_path: Path) -> None:
    (tmp_path / "KAIA").mkdir()
    planned = plan_destination(tmp_path, SAMPLE, flat(), "mp3")
    assert reuse_folders_by_case(planned, tmp_path) == tmp_path / f"{SAMPLE_NAME}.mp3"


def test_a_file_named_like_the_artist_isnt_taken_for_their_folder(tmp_path: Path) -> None:
    make_file(tmp_path / "kaia")
    assert planned_and_reused(tmp_path, SAMPLE, Settings()).parent.name == "Kaia"


@pytest.mark.parametrize(
    ("name", "existing", "expected"),
    [
        ("KAIA", ["Hazy", "Kaia"], "Kaia"),
        ("Kaia", ["KAIA", "Kaia"], "Kaia"),
        ("kaia", ["KAIA", "Kaia"], "KAIA"),
        (f"Beyonc{E_ACUTE}", [f"BEYONCE{COMBINING_ACUTE}"], f"BEYONCE{COMBINING_ACUTE}"),
        ("Lou V", ["Kaia", "Lou V2"], "Lou V"),
        ("Kaia", [], "Kaia"),
    ],
)
def test_the_folder_to_reuse_is_the_same_name_or_the_first_that_differs_only_by_case(
    name: str, existing: list[str], expected: str
) -> None:
    assert library.folder_name_to_reuse(name, existing) == expected


def test_a_library_folder_that_doesnt_exist_yet_still_gets_artist_folders(tmp_path: Path) -> None:
    library_dir = tmp_path / "Music" / "sc-mp3"
    destination = planned_and_reused(library_dir, SAMPLE, Settings())
    assert destination == library_dir / "Kaia" / f"{SAMPLE_NAME}.mp3"


def refuse_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(path: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(library.os, "scandir", refuse)


def test_a_library_that_cant_be_listed_gets_a_new_artist_folder_and_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    refuse_listing(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="scmp3.core.library"):
        destination = planned_and_reused(tmp_path, SAMPLE, Settings())
    assert destination == tmp_path / "Kaia" / f"{SAMPLE_NAME}.mp3"
    assert "Couldn't look for a folder to reuse: Permission denied" in caplog.text


def test_folders_that_cant_be_listed_give_one_warning_between_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The artist's folder and the album's inside it are both looked for.
    refuse_listing(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="scmp3.core.library"):
        planned_and_reused(tmp_path, SAMPLE, Settings(album_subfolders=True))
    assert len(caplog.records) == 1


def test_a_look_for_folders_to_reuse_can_leave_the_warning_to_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    refuse_listing(monkeypatch)
    planned = plan_destination(tmp_path, SAMPLE, Settings(), "mp3")
    with caplog.at_level(logging.WARNING, logger="scmp3.core.library"):
        assert reuse_folders_by_case(planned, tmp_path, warn=False) == planned
    assert caplog.records == []


def test_with_album_folders_an_album_goes_inside_its_artists_folder(tmp_path: Path) -> None:
    destination = plan_destination(tmp_path, SAMPLE, Settings(album_subfolders=True), "mp3")
    assert destination == tmp_path / "Kaia" / "Night Shift" / f"03 {SAMPLE_NAME}.mp3"


def test_with_album_folders_a_single_goes_in_its_artists_folder_only(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive", album="Midnight Drive")
    destination = plan_destination(tmp_path, tags, Settings(album_subfolders=True), "mp3")
    assert destination == tmp_path / "Kaia" / "Kaia - Midnight Drive.mp3"


def test_an_album_folder_that_differs_only_by_case_is_reused(tmp_path: Path) -> None:
    (tmp_path / "kaia" / "night shift").mkdir(parents=True)
    destination = planned_and_reused(tmp_path, SAMPLE, Settings(album_subfolders=True))
    assert (destination.parent.parent.name, destination.parent.name) == ("kaia", "night shift")


def test_without_artist_folders_a_case_variant_album_folder_is_reused(tmp_path: Path) -> None:
    (tmp_path / "kaia - night shift").mkdir()
    destination = planned_and_reused(tmp_path, SAMPLE, flat(album_subfolders=True))
    assert destination.parent.name == "kaia - night shift"


def test_turning_artist_folders_on_leaves_saved_files_where_they_are(tmp_path: Path) -> None:
    saved_before = make_file(plan_destination(tmp_path, SAMPLE, flat(), "mp3"))
    destination = plan_destination(tmp_path, SAMPLE, Settings(), "mp3")
    assert destination == tmp_path / "Kaia" / saved_before.name
    assert resolve_collision(destination, "skip") == destination
    assert saved_before.is_file()


def test_the_default_library_is_inside_scmp3_home(scmp3_home: Path) -> None:
    settings = Settings()
    destination = plan_destination(settings.library_dir, SAMPLE, settings, "mp3")
    assert destination.is_relative_to(scmp3_home / "music" / "sc-mp3")


@pytest.fixture
def windows_path_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    """Windows' MAX_PATH, on whichever OS the test runs."""
    monkeypatch.setattr(library, "MAX_PATH_UNITS", 259)
    return 259


def folder_of_length(tmp_path: Path, length: int) -> Path:
    """A path under tmp_path that's `length` characters long. Nothing is created."""
    padding = length - len(str(tmp_path.absolute())) - 1
    assert padding > 0
    return tmp_path / ("L" * padding)


def test_a_name_too_long_for_windows_paths_is_shortened(
    windows_path_limit: int, tmp_path: Path
) -> None:
    # 259 - 200 - 1 for the separator - 11 for a temp name - 4 for .mp3 leaves 43.
    folder = folder_of_length(tmp_path, 200)
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive (feat. Lou V) [Extended Mix]")
    destination = plan_destination(folder, tags, flat(), "mp3")
    assert destination == folder / "Kaia - Midnight Drive (feat. Lou V) [Extend.mp3"
    assert len(str(destination)) + library.TEMP_NAME_EXTRA == windows_path_limit


def test_a_name_that_fits_the_windows_path_isnt_shortened(
    windows_path_limit: int, tmp_path: Path
) -> None:
    folder = folder_of_length(tmp_path, 200)
    assert plan_destination(folder, SAMPLE, flat(), "mp3").name == f"{SAMPLE_NAME}.mp3"


def test_a_name_is_never_shortened_below_20_characters(
    windows_path_limit: int, tmp_path: Path
) -> None:
    # This leaves 19 for the name, one short of the minimum.
    folder = folder_of_length(tmp_path, 224)
    with pytest.raises(ScmpError) as error_info:
        plan_destination(folder, SAMPLE, flat(), "mp3")
    assert error_info.value.kind is ErrorKind.disk
    assert error_info.value.detail is not None
    assert "library folder path is too long" in error_info.value.detail
    # The UI says to choose a shorter folder, rather than to check the drive.
    assert error_info.value.reason == "path_too_long"


def test_a_short_name_still_fits_a_folder_with_little_room(
    windows_path_limit: int, tmp_path: Path
) -> None:
    folder = folder_of_length(tmp_path, 224)
    tags = dataclasses.replace(SAMPLE, title="Song")
    destination = plan_destination(folder, tags, flat(title_style="all_in_title"), "mp3")
    assert destination == folder / "Song.mp3"


def test_without_a_path_limit_a_deep_folder_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(library, "MAX_PATH_UNITS", None)
    folder = folder_of_length(tmp_path, 300)
    assert plan_destination(folder, SAMPLE, Settings(), "mp3").name == f"{SAMPLE_NAME}.mp3"


def test_the_artist_folder_counts_toward_the_windows_path_limit(
    windows_path_limit: int, tmp_path: Path
) -> None:
    # "Kaia" and its separator make this the same 200-character folder as above.
    library_dir = folder_of_length(tmp_path, 195)
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive (feat. Lou V) [Extended Mix]")
    destination = plan_destination(library_dir, tags, Settings(), "mp3")
    assert destination == library_dir / "Kaia" / "Kaia - Midnight Drive (feat. Lou V) [Extend.mp3"
    assert len(str(destination)) + library.TEMP_NAME_EXTRA == windows_path_limit


def test_artist_and_album_folders_both_count_toward_the_windows_path_limit(
    windows_path_limit: int, tmp_path: Path
) -> None:
    library_dir = folder_of_length(tmp_path, 183)
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive (feat. Lou V) [Extended Mix]")
    destination = plan_destination(library_dir, tags, Settings(album_subfolders=True), "mp3")
    assert destination.parent == library_dir / "Kaia" / "Night Shift"
    assert destination.name == "03 Kaia - Midnight Drive (feat. Lou V) [Ext.mp3"
    assert len(str(destination)) + library.TEMP_NAME_EXTRA == windows_path_limit


def test_a_long_artist_folder_can_leave_no_room_for_any_name(
    windows_path_limit: int, tmp_path: Path
) -> None:
    # 150 for the library and 101 for the artist folder leave less than 20 within 259.
    library_dir = folder_of_length(tmp_path, 150)
    tags = dataclasses.replace(SAMPLE, album_artist="a" * 120)
    with pytest.raises(ScmpError) as error_info:
        plan_destination(library_dir, tags, Settings(), "mp3")
    assert error_info.value.kind is ErrorKind.disk


POLICIES: list[CollisionPolicy] = ["ask", "keep_both", "replace", "skip"]


@pytest.mark.parametrize("policy", POLICIES)
def test_a_free_name_is_used_under_every_policy(policy: CollisionPolicy, tmp_path: Path) -> None:
    free = tmp_path / "Song.mp3"
    ask = fail_on_purpose("asked about a name nobody uses")
    assert resolve_collision(free, policy, ask=ask) == free


def test_replace_keeps_the_taken_name(tmp_path: Path) -> None:
    taken = make_file(tmp_path / "Song.mp3")
    assert resolve_collision(taken, "replace") == taken


def test_skip_returns_none(tmp_path: Path) -> None:
    taken = make_file(tmp_path / "Song.mp3")
    assert resolve_collision(taken, "skip") is None


def test_keep_both_adds_2_then_3(tmp_path: Path) -> None:
    taken = make_file(tmp_path / "Kaia - Song.mp3")
    second = resolve_collision(taken, "keep_both")
    assert second == tmp_path / "Kaia - Song (2).mp3"
    make_file(second)
    assert resolve_collision(taken, "keep_both") == tmp_path / "Kaia - Song (3).mp3"


def test_keep_both_shortens_a_long_name_to_stay_within_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = tmp_path / ("a" * 146 + ".mp3")
    pretend_taken(monkeypatch, taken)
    second = resolve_collision(taken, "keep_both")
    assert second == tmp_path / ("a" * 142 + " (2).mp3")


def test_keep_both_stays_within_the_windows_path_limit(
    windows_path_limit: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = folder_of_length(tmp_path, 200)
    tags = dataclasses.replace(SAMPLE, title="Midnight Drive (feat. Lou V) [Extended Mix]")
    taken = plan_destination(folder, tags, flat(), "mp3")
    pretend_taken(monkeypatch, taken)
    second = resolve_collision(taken, "keep_both")
    assert second == folder / "Kaia - Midnight Drive (feat. Lou V) [Ex (2).mp3"
    assert len(str(second)) + library.TEMP_NAME_EXTRA == windows_path_limit


@pytest.mark.parametrize(
    ("choice", "expected_name"),
    [("replace", "Song.mp3"), ("keep_both", "Song (2).mp3"), ("skip", None)],
)
def test_ask_applies_the_users_choice(
    choice: library.CollisionChoice, expected_name: str | None, tmp_path: Path
) -> None:
    taken = make_file(tmp_path / "Song.mp3")
    asked: list[Path] = []

    def ask(path: Path) -> library.CollisionChoice:
        asked.append(path)
        return choice

    result = resolve_collision(taken, "ask", ask=ask)
    assert asked == [taken]
    assert result == (tmp_path / expected_name if expected_name else None)


def test_ask_without_a_callback_is_an_internal_error(tmp_path: Path) -> None:
    taken = make_file(tmp_path / "Song.mp3")
    with pytest.raises(ScmpError) as error_info:
        resolve_collision(taken, "ask")
    assert error_info.value.kind is ErrorKind.internal


@pytest.mark.parametrize(("policy", "answer"), [("ask", "ask"), ("ask", "overwrite"), ("bad", "")])
def test_an_unknown_choice_is_an_internal_error(policy: str, answer: str, tmp_path: Path) -> None:
    taken = make_file(tmp_path / "Song.mp3")
    with pytest.raises(ScmpError) as error_info:
        resolve_collision(taken, policy, ask=lambda path: answer)  # type: ignore[arg-type]
    assert error_info.value.kind is ErrorKind.internal
