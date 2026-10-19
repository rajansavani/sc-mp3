"""Tests for library filenames, destinations and collisions."""

import dataclasses
import random
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from scmp3.core import library
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import (
    RESERVED_NAMES,
    plan_destination,
    render_filename,
    resolve_collision,
    sanitize,
)
from scmp3.core.models import CollisionPolicy, Settings, Tags, TitleStyle

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
NBSP = chr(0xA0)
COMBINING_ACUTE = chr(0x301)
E_ACUTE = chr(0xE9)
# U with diaeresis and macron: one UTF-16 unit composed, three decomposed.
U_DIAERESIS_MACRON = chr(0x1D6)
# A CJK character: three UTF-8 bytes.
NIGHT = chr(0x591C)
# Mathematical bold A, outside the BMP: four UTF-8 bytes and two UTF-16 units.
BOLD_A = chr(0x1D400)
HIGH_SURROGATE = chr(0xD83D)


@pytest.fixture(autouse=True)
def no_path_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave out Windows' MAX_PATH, so the temp folder's length can't change any name."""
    monkeypatch.setattr(library, "MAX_PATH_UNITS", None)


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def pretend_taken(monkeypatch: pytest.MonkeyPatch, *taken: Path) -> None:
    """Make exists() true for these paths only, for names too long to create here."""
    monkeypatch.setattr(Path, "exists", lambda path, *args, **kwargs: path in taken)


def fail_on_purpose(message: str) -> Callable[..., None]:
    """A stand-in that fails the test if it's ever called."""

    def stand_in(*args: object) -> None:
        pytest.fail(message)

    return stand_in


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def assert_within_name_limits(name: str) -> None:
    assert len(name) <= 150
    assert len(name.encode("utf-8")) <= 240
    assert utf16_units(unicodedata.normalize("NFD", name)) <= 240


@pytest.mark.parametrize("character", list('\\/:*?"<>|'))
def test_sanitize_replaces_each_illegal_character_with_a_dash(character: str) -> None:
    assert sanitize(f"AC{character}DC.mp3") == "AC-DC.mp3"


@pytest.mark.parametrize(
    "character", ["\x00", "\x01", "\t", "\n", "\r", "\x1b", "\x1f", "\x7f", "\x85", "\x9f"]
)
def test_sanitize_replaces_control_characters_with_a_dash(character: str) -> None:
    assert sanitize(f"Night{character}Shift.mp3") == "Night-Shift.mp3"


def test_sanitize_removes_half_a_surrogate_pair() -> None:
    name = sanitize(f"Song {HIGH_SURROGATE}.mp3")
    assert name == "Song.mp3"
    assert name.encode("utf-8")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Night   Shift", "Night Shift"),
        (f"Night{NBSP} Shift", "Night Shift"),
        ("Drum--Bass", "Drum-Bass"),
        ("Kaia - - Night Shift", "Kaia - Night Shift"),
        ("Kaia -- -- Night Shift", "Kaia - Night Shift"),
    ],
)
def test_sanitize_collapses_repeated_spaces_and_dashes(name: str, expected: str) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize("device", sorted(RESERVED_NAMES))
def test_sanitize_adds_an_underscore_to_every_reserved_name(device: str) -> None:
    assert sanitize(device) == f"{device}_"
    assert sanitize(f"{device}.mp3") == f"{device}_.mp3"


def test_com0_and_lpt0_are_reserved_too() -> None:
    assert {"COM0", "LPT0"} <= RESERVED_NAMES


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("con", "con_"),
        ("Con.mp3", "Con_.mp3"),
        ("cOm1.m4a", "cOm1_.m4a"),
        ("lpt9.MP3", "lpt9_.MP3"),
        ("nul.tar.gz", "nul_.tar.gz"),
        ("aux .mp3", "aux_.mp3"),
        ("prn.", "prn_"),
    ],
)
def test_sanitize_matches_reserved_names_in_any_case_and_with_extensions(
    name: str, expected: str
) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize(
    "name", ["Con Calma.mp3", "COM10.mp3", "COM00.mp3", "Console.mp3", "Nullify.mp3", "LPT.mp3"]
)
def test_sanitize_leaves_names_that_only_look_reserved(name: str) -> None:
    assert sanitize(name) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("KAIASO~1.mp3", "KAIASO~1_.mp3"),
        ("kaiaso~1.MP3", "kaiaso~1_.MP3"),
        ("NIGHT~12", "NIGHT~12_"),
        ("AB12F3~5.mp3", "AB12F3~5_.mp3"),
        ("A~1", "A~1_"),
        ("~1.m4a", "~1_.m4a"),
    ],
)
def test_sanitize_adds_an_underscore_to_a_windows_short_name(name: str, expected: str) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "KAIASOME~1.mp3",
        "NIGHTS~12",
        "Kaia ~1.mp3",
        "Kaia~X.mp3",
        "AB~1X.mp3",
        "Kai~1.X.mp3",
        "Kaia-1.mp3",
    ],
)
def test_sanitize_leaves_names_that_cant_be_short_names(name: str) -> None:
    assert sanitize(name) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Wait...", "Wait"),
        ("Wait . . .", "Wait"),
        ("Night Shift  ", "Night Shift"),
        ("Wait....mp3", "Wait.mp3"),
        ("Song.mp3 ", "Song.mp3"),
        ("Song.mp3.", "Song.mp3"),
    ],
)
def test_sanitize_strips_trailing_dots_and_spaces(name: str, expected: str) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (".hidden.mp3", "hidden.mp3"),
        ("...And You Will Know Us.mp3", "And You Will Know Us.mp3"),
        ("  - Night Shift.mp3", "Night Shift.mp3"),
        ("What?.mp3", "What.mp3"),
    ],
)
def test_sanitize_strips_leading_dots_and_dangling_dashes(name: str, expected: str) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Song -.mp3 ", "Song.mp3"), ("What?.mp3?", "What.mp3"), ("-CON|.mp3<", "CON_.mp3")],
)
def test_junk_after_the_extension_doesnt_hide_it(name: str, expected: str) -> None:
    assert sanitize(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("", "Untitled"), ("???", "Untitled"), ("...", "Untitled"), (" - ", "Untitled")],
)
def test_sanitize_names_an_empty_result_untitled(name: str, expected: str) -> None:
    assert sanitize(name) == expected
    assert sanitize(f"{name}.mp3") == f"{expected}.mp3"


def test_sanitize_caps_the_whole_name_at_150_characters_and_keeps_the_extension() -> None:
    assert sanitize("a" * 200 + ".mp3") == "a" * 146 + ".mp3"
    assert sanitize("b" * 200) == "b" * 150


def test_sanitize_leaves_a_name_of_exactly_150_characters_whole() -> None:
    name = "a" * 146 + ".m4a"
    assert sanitize(name) == name


def test_sanitize_caps_the_name_at_240_utf8_bytes() -> None:
    assert sanitize(NIGHT * 100 + ".mp3") == NIGHT * 78 + ".mp3"


def test_sanitize_caps_characters_outside_the_bmp_by_bytes_and_keeps_them_whole() -> None:
    name = sanitize(BOLD_A * 130 + ".mp3")
    assert name == BOLD_A * 59 + ".mp3"
    assert_within_name_limits(name)


def test_sanitize_caps_the_name_at_240_utf16_units_counted_decomposed() -> None:
    # 100 of these are 204 UTF-8 bytes, but 304 UTF-16 units once decomposed, as HFS+ stores them.
    name = sanitize(U_DIAERESIS_MACRON * 100 + ".mp3")
    assert name == U_DIAERESIS_MACRON * 78 + ".mp3"
    assert_within_name_limits(name)


def test_the_cap_never_parts_a_character_from_its_combining_mark() -> None:
    # q has no precomposed acute form, so NFC keeps the mark as its own code point.
    assert sanitize("a" * 145 + "q" + COMBINING_ACUTE + ".mp3") == "a" * 145 + ".mp3"


def test_sanitize_trims_a_space_the_cap_lands_on() -> None:
    assert sanitize("a" * 145 + " bcd.mp3") == "a" * 145 + ".mp3"


def test_sanitize_keeps_a_reserved_name_within_the_cap() -> None:
    assert sanitize("CON." + "a" * 142 + ".mp3") == "CON_." + "a" * 141 + ".mp3"


def test_sanitize_normalizes_to_nfc() -> None:
    decomposed = f"Beyonce{COMBINING_ACUTE} - Halo.mp3"
    assert sanitize(decomposed) == f"Beyonc{E_ACUTE} - Halo.mp3"


def test_sanitize_counts_characters_after_nfc_for_the_cap() -> None:
    # Decomposed, this is 200 code points; composed, it fits the 150-character cap.
    assert sanitize(f"e{COMBINING_ACUTE}" * 100 + ".mp3") == E_ACUTE * 100 + ".mp3"


@pytest.mark.parametrize(
    "name", ["Kaia - Midnight Drive (feat. Lou V).mp3", "Sälen 夜 - Ünïcödé.m4a", "AC-DC"]
)
def test_sanitize_leaves_a_clean_name_alone(name: str) -> None:
    assert sanitize(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "AC/DC: Live?? .mp3",
        "con.mp3",
        "  ...x - - y...",
        "a" * 160 + " .mp3",
        "",
        "Song -.mp3 ",
        "What?.mp3?",
        "-CON|.mp3<",
    ],
)
def test_sanitizing_twice_changes_nothing_more(name: str) -> None:
    once = sanitize(name)
    assert sanitize(once) == once


FUZZ_PIECES = [
    *'aZ09 .-_~()[]{}\\/:*?"<>|',
    *("\x00", "\t", NBSP, COMBINING_ACUTE, E_ACUTE, NIGHT, BOLD_A, HIGH_SURROGATE),
    *("CON", "com1", "KAIASO~1", ".mp3", ".m4a", "{title}", "{track:02}", "{year}"),
]


def random_texts(count: int, seed: int) -> list[str]:
    generator = random.Random(seed)
    return [
        "".join(generator.choices(FUZZ_PIECES, k=generator.randint(0, 60))) for _ in range(count)
    ]


def assert_follows_the_name_rules(name: str) -> None:
    assert not library.ILLEGAL_CHARACTERS.search(name)
    assert not any(ord(char) in library.SURROGATES for char in name)
    assert unicodedata.is_normalized("NFC", name)
    assert name[0] not in " .-"
    assert name[-1] not in " ."
    assert name.partition(".")[0].rstrip(" ").upper() not in RESERVED_NAMES
    assert_within_name_limits(name)


def stem_of(name: str) -> str:
    match = library.EXTENSION.search(name)
    if match is None:
        return name
    return name[: match.start()]


def test_random_names_always_come_out_safe_and_stable() -> None:
    for text in random_texts(5000, seed=16):
        name = sanitize(text)
        assert_follows_the_name_rules(name)
        stem = stem_of(name)
        assert not (len(stem) <= 8 and library.SHORT_NAME.fullmatch(stem))
        assert sanitize(name) == name, repr(text)


def test_random_templates_always_render_safe_names() -> None:
    for template in random_texts(5000, seed=61):
        assert_follows_the_name_rules(render_filename(template, SAMPLE))


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


def test_plan_destination_uses_artist_dash_title_by_default(tmp_path: Path) -> None:
    assert plan_destination(tmp_path, SAMPLE, Settings(), "mp3") == tmp_path / f"{SAMPLE_NAME}.mp3"


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
    settings = Settings(album_subfolders=True)
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3")
    assert destination == tmp_path / "Kaia - Night Shift" / f"03 {SAMPLE_NAME}.mp3"


def test_album_subfolders_leave_out_a_missing_track_number(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, track_no=None)
    destination = plan_destination(tmp_path, tags, Settings(album_subfolders=True), "mp3")
    assert destination == tmp_path / "Kaia - Night Shift" / f"{SAMPLE_NAME}.mp3"


def test_album_subfolders_dont_repeat_a_track_number_the_template_has(tmp_path: Path) -> None:
    settings = Settings(album_subfolders=True, filename_template="{track} {title}")
    destination = plan_destination(tmp_path, SAMPLE, settings, "mp3")
    assert destination.name == "3 Midnight Drive (feat. Lou V).mp3"


def test_a_track_with_no_album_stays_at_the_top_of_the_library(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album=" ")
    destination = plan_destination(tmp_path, tags, Settings(album_subfolders=True), "mp3")
    assert destination == tmp_path / f"{SAMPLE_NAME}.mp3"


def test_the_album_folder_name_is_sanitized(tmp_path: Path) -> None:
    tags = dataclasses.replace(SAMPLE, album="Night/Shift: Vol. 2...", album_artist="")
    destination = plan_destination(tmp_path, tags, Settings(album_subfolders=True), "mp3")
    assert destination.parent == tmp_path / "Night-Shift- Vol. 2"


def test_a_title_shaped_like_a_short_name_cant_reach_another_file(tmp_path: Path) -> None:
    # On NTFS with short names on, KAIASO~1.mp3 is another name for this file.
    make_file(tmp_path / "Kaia Something Long.mp3")
    tags = dataclasses.replace(SAMPLE, title="KAIASO~1")
    destination = plan_destination(tmp_path, tags, Settings(title_style="all_in_title"), "mp3")
    assert destination.name == "KAIASO~1_.mp3"
    assert resolve_collision(destination, "skip") == destination


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
    destination = plan_destination(folder, tags, Settings(), "mp3")
    assert destination == folder / "Kaia - Midnight Drive (feat. Lou V) [Extend.mp3"
    assert len(str(destination)) + library.TEMP_NAME_EXTRA == windows_path_limit


def test_a_name_that_fits_the_windows_path_isnt_shortened(
    windows_path_limit: int, tmp_path: Path
) -> None:
    folder = folder_of_length(tmp_path, 200)
    assert plan_destination(folder, SAMPLE, Settings(), "mp3").name == f"{SAMPLE_NAME}.mp3"


def test_a_name_is_never_shortened_below_20_characters(
    windows_path_limit: int, tmp_path: Path
) -> None:
    # This leaves 19 for the name, one short of the minimum.
    folder = folder_of_length(tmp_path, 224)
    with pytest.raises(ScmpError) as error_info:
        plan_destination(folder, SAMPLE, Settings(), "mp3")
    assert error_info.value.kind is ErrorKind.disk
    assert error_info.value.detail is not None
    assert "library folder path is too long" in error_info.value.detail


def test_a_short_name_still_fits_a_folder_with_little_room(
    windows_path_limit: int, tmp_path: Path
) -> None:
    folder = folder_of_length(tmp_path, 224)
    tags = dataclasses.replace(SAMPLE, title="Song")
    destination = plan_destination(folder, tags, Settings(title_style="all_in_title"), "mp3")
    assert destination == folder / "Song.mp3"


def test_without_a_path_limit_a_deep_folder_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(library, "MAX_PATH_UNITS", None)
    folder = folder_of_length(tmp_path, 300)
    assert plan_destination(folder, SAMPLE, Settings(), "mp3").name == f"{SAMPLE_NAME}.mp3"


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
    taken = plan_destination(folder, tags, Settings(), "mp3")
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
