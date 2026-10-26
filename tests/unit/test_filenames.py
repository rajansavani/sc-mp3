"""Tests for safe file and folder names: characters, reserved names and length limits."""

import random
import unicodedata

import pytest

from scmp3.core import filenames
from scmp3.core.filenames import RESERVED_NAMES, sanitize
from scmp3.core.library import render_filename
from scmp3.core.models import Tags

# For the random templates, with every token filled in.
TAGS = Tags(
    title="Midnight Drive (feat. Lou V)",
    artist="Kaia",
    album="Night Shift",
    album_artist="Kaia",
    year=2026,
    track_no=3,
    track_total=12,
)

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
    assert not filenames.ILLEGAL_CHARACTERS.search(name)
    assert not any(ord(char) in filenames.SURROGATES for char in name)
    assert unicodedata.is_normalized("NFC", name)
    assert name[0] not in " .-"
    assert name[-1] not in " ."
    assert name.partition(".")[0].rstrip(" ").upper() not in RESERVED_NAMES
    assert_within_name_limits(name)


def stem_of(name: str) -> str:
    match = filenames.EXTENSION.search(name)
    if match is None:
        return name
    return name[: match.start()]


def test_random_names_always_come_out_safe_and_stable() -> None:
    for text in random_texts(5000, seed=16):
        name = sanitize(text)
        assert_follows_the_name_rules(name)
        stem = stem_of(name)
        assert not (len(stem) <= 8 and filenames.SHORT_NAME.fullmatch(stem))
        assert sanitize(name) == name, repr(text)


def test_random_templates_always_render_safe_names() -> None:
    for template in random_texts(5000, seed=61):
        assert_follows_the_name_rules(render_filename(template, TAGS))


@pytest.mark.parametrize(
    "unit",
    [
        "\U0001f1fa\U0001f1f8",  # a flag
        "\U0001f469\u200d\u2764\ufe0f\u200d\U0001f468",  # a ZWJ sequence
        "\U0001f44d\U0001f3fd",  # a skin tone
        "1\ufe0f\u20e3",  # a keycap
        "\u2764\ufe0f",  # a variation selector
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # tags
        "e\u0301\u0302",  # a letter with two marks
    ],
)
def test_the_cap_never_cuts_inside_a_character_as_people_see_it(unit: str) -> None:
    limits = filenames.NAME_LIMITS.less(4)
    for padding in range(len(unit) + 1):
        stem = filenames.cap_length("a" * padding + unit * 200, limits)
        rest = stem[padding:]
        assert rest == unit * (len(rest) // len(unit)), (padding, ascii(stem[-12:]))


def test_the_cap_never_cuts_a_conjunct_after_its_virama() -> None:
    conjunct = "\u0915\u094d\u0937"  # Devanagari ksha: ka, virama, ssa
    limits = filenames.NAME_LIMITS.less(4)
    for padding in range(3):
        stem = filenames.cap_length("a" * padding + conjunct * 200, limits)
        rest = stem[padding:]
        assert rest == conjunct * (len(rest) // len(conjunct)), padding
