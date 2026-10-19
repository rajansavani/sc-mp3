"""Tests for turning pasted or dropped text into typed inputs."""

import os
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from scmp3.core import inputs
from scmp3.core.inputs import (
    FILE_NOT_FOUND,
    FOLDER_NOT_FILE,
    MAX_CHARACTERS,
    MAX_ITEMS,
    MAX_WORDS,
    NOT_RETAGGABLE,
    PASTE_TOO_LONG,
    ParsedInput,
    classify,
    parse_inputs,
)
from scmp3.core.urls import NOT_SUPPORTED, YOUTUBE_PLAYLIST

# The drive the fake drive table reports as a mapped network drive.
REMOTE_DRIVE = "R:"
DRIVE_FIXED, DRIVE_REMOTE = 3, 4


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test here that tries to open a connection."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("this test tried to use the network")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(autouse=True)
def no_share_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test here that checks a network path on disk: a share, /net, or the remote drive."""
    for name in ("exists", "isdir", "isfile"):
        monkeypatch.setattr(os.path, name, refuse_shares(getattr(os.path, name)))


def refuse_shares(check: Callable[[object], bool]) -> Callable[[object], bool]:
    def guarded(path: object) -> bool:
        text = os.fspath(path) if isinstance(path, (str, os.PathLike)) else ""
        is_share = text[:2] in ("//", "\\\\", "/\\", "\\/")
        if is_share or text.startswith("/net/") or text[:2].upper() == REMOTE_DRIVE:
            raise AssertionError(f"this test checked a network path: {text}")
        return check(path)

    return guarded


@pytest.fixture(autouse=True)
def fake_drive_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report REMOTE_DRIVE as a mapped network drive and every other drive as a local disk."""

    def drive_type(root: str) -> int:
        return DRIVE_REMOTE if root.upper() == REMOTE_DRIVE + "\\" else DRIVE_FIXED

    monkeypatch.setattr(inputs, "_get_drive_type", drive_type)


VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "pasted",
    [
        pytest.param("hello", id="a word"),
        pytest.param("Check", id="a capitalized word"),
        pytest.param("https://example.com/artist/track", id="another site"),
        pytest.param("https://notsoundcloud.com/artist/track", id="a lookalike host"),
        pytest.param("ftp://soundcloud.com/artist/track", id="not http"),
        pytest.param("https://[broken/track", id="a broken host"),
        pytest.param("song.wav", id="a file name that isn't audio"),
        pytest.param("https://on.soundcloud.com.example.net/AbC", id="a host under another"),
        pytest.param("https://soundcloud.com@example.net/a/b", id="a host as userinfo"),
        pytest.param("soundcloud.com@example.net/a/b", id="a host as userinfo, no scheme"),
        pytest.param("//192.0.2.2/artist/track", id="a protocol-relative link elsewhere"),
        pytest.param("//nas/music/song.mp3", id="a share written with forward slashes"),
        # Windows says these exist, as devices or by dropping the dots.
        *(pytest.param(word, id=f"the word {word}") for word in ("con", "Con", "nul", "CONIN$")),
        *(pytest.param(word, id=f"the word {word}") for word in ("con.", "...", "....")),
        pytest.param("C:", id="a bare drive"),
        pytest.param("\\", id="a backslash"),
        pytest.param("~5", id="a tilde before a number"),
        pytest.param("and/or", id="a word with a slash"),
        pytest.param("AC/DC", id="a name with a slash"),
    ],
)
def test_text_that_isnt_a_link_or_a_file_is_unsupported(pasted: str) -> None:
    assert classify(pasted) == ParsedInput("unsupported", pasted, reason=NOT_SUPPORTED)


@pytest.mark.parametrize(
    "text",
    [
        # Text from JavaScript can hold lone surrogates.
        pytest.param("on.soundcloud.com/AbC?si=\udcff", id="lone surrogate in a link"),
        pytest.param("file:///%FF.mp3", id="file URI that isn't UTF-8"),
        pytest.param("song\x00.mp3", id="NUL in a path"),
        # macOS and Linux look up the user after ~, which raises for these.
        pytest.param("~a\x00", id="NUL after a tilde"),
        pytest.param("~\ud800", id="lone surrogate after a tilde"),
        pytest.param("\ud800 ~x\ud83d", id="lone surrogates around a tilde"),
        pytest.param("~a\x00.mp3", id="NUL after a tilde, audio name"),
        pytest.param(
            "w.soundcloud.com/player/?url=" * 400 + "https://api.soundcloud.com/tracks/1",
            id="embeds nested 400 deep",
        ),
    ],
)
def test_odd_text_is_unsupported_rather_than_an_error(text: str) -> None:
    parsed = parse_inputs(text)
    assert parsed
    assert all(item.kind == "unsupported" for item in parsed)


def track_urls(text: str) -> list[str | None]:
    return [parsed.url for parsed in parse_inputs(text) if parsed.kind == "soundcloud_track"]


@pytest.mark.parametrize(
    "text",
    [
        "Found this '90s remix https://soundcloud.com/artist/track and it's great",
        "'Til the end https://soundcloud.com/artist/track - don't skip it",
        'My friend said "listen to https://soundcloud.com/artist/track" yesterday',
    ],
)
def test_a_link_inside_quotes_or_apostrophes_is_found(text: str) -> None:
    assert track_urls(text) == ["https://soundcloud.com/artist/track"]


def test_an_ordinary_word_never_gets_a_file_reason() -> None:
    text = "Escucha esto con tus amigos... https://soundcloud.com/artist/track"
    parsed = parse_inputs(text)
    assert parsed[-1].url == "https://soundcloud.com/artist/track"
    assert {item.reason for item in parsed[:-1]} == {NOT_SUPPORTED}


def test_two_quoted_links_in_one_run_are_both_found() -> None:
    text = '"https://soundcloud.com/artist/track https://soundcloud.com/artist/other"'
    assert track_urls(text) == [
        "https://soundcloud.com/artist/track",
        "https://soundcloud.com/artist/other",
    ]


def test_a_link_in_curly_quotes_in_a_sentence_is_found() -> None:
    text = "Listen to \u201chttps://soundcloud.com/artist/track\u201d tonight"
    assert track_urls(text) == ["https://soundcloud.com/artist/track"]


def test_parse_inputs_splits_on_spaces_tabs_and_newlines_in_order() -> None:
    text = (
        "https://youtu.be/dQw4w9WgXcQ\r\n"
        "https://soundcloud.com/artist/track\thttps://soundcloud.com/artist/sets/album\n\n"
        "   on.soundcloud.com/AbC123  \n"
    )
    assert [(parsed.kind, parsed.url) for parsed in parse_inputs(text)] == [
        ("youtube_video", VIDEO),
        ("soundcloud_track", "https://soundcloud.com/artist/track"),
        ("soundcloud_set", "https://soundcloud.com/artist/sets/album"),
        ("soundcloud_short", "https://on.soundcloud.com/AbC123"),
    ]


def test_parse_inputs_keeps_the_first_of_links_that_normalize_the_same() -> None:
    text = (
        "https://soundcloud.com/artist/track?si=abc "
        "https://M.SoundCloud.com/artist/track/ "
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ "
        "https://soundcloud.com/artist/track?utm_source=clipboard "
        "https://youtu.be/dQw4w9WgXcQ?t=3"
    )
    assert parse_inputs(text) == [
        ParsedInput(
            "soundcloud_track",
            "https://soundcloud.com/artist/track?si=abc",
            url="https://soundcloud.com/artist/track",
        ),
        ParsedInput("youtube_video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", url=VIDEO),
    ]


def test_parse_inputs_rejects_words_with_a_reason_and_drops_repeated_ones() -> None:
    text = "check this https://soundcloud.com/artist/track check it"
    assert parse_inputs(text) == [
        ParsedInput("unsupported", "check", reason=NOT_SUPPORTED),
        ParsedInput("unsupported", "this", reason=NOT_SUPPORTED),
        ParsedInput(
            "soundcloud_track",
            "https://soundcloud.com/artist/track",
            url="https://soundcloud.com/artist/track",
        ),
        ParsedInput("unsupported", "it", reason=NOT_SUPPORTED),
    ]


@pytest.mark.parametrize("text", ["", "   ", "\n\t\r\n"])
def test_parse_inputs_of_blank_text_is_empty(text: str) -> None:
    assert parse_inputs(text) == []


TOO_LONG = ParsedInput("unsupported", "", reason=PASTE_TOO_LONG)


def numbered_links(count: int) -> str:
    return " ".join(f"https://soundcloud.com/artist/track-{number}" for number in range(count))


def test_a_paste_of_exactly_the_item_limit_is_read_whole() -> None:
    parsed = parse_inputs(numbered_links(MAX_ITEMS))
    assert len(parsed) == MAX_ITEMS
    assert TOO_LONG not in parsed


def test_items_past_the_limit_are_skipped_with_one_reason() -> None:
    parsed = parse_inputs(numbered_links(MAX_ITEMS + 50))
    assert len(parsed) == MAX_ITEMS + 1
    assert parsed[-2].url == f"https://soundcloud.com/artist/track-{MAX_ITEMS - 1}"
    assert parsed[-1] == TOO_LONG


def test_repeats_dont_count_toward_the_item_limit() -> None:
    link = "https://soundcloud.com/artist/track"
    parsed = parse_inputs(f"{link} hello " * (MAX_ITEMS + 1))
    assert parsed == [
        ParsedInput("soundcloud_track", link, url=link),
        ParsedInput("unsupported", "hello", reason=NOT_SUPPORTED),
    ]


def numbered_words(count: int) -> str:
    return " ".join(f"word{number}" for number in range(count))


def test_words_dont_push_links_past_the_item_limit() -> None:
    parsed = parse_inputs(f"{numbered_words(MAX_ITEMS * 3)} {numbered_links(MAX_ITEMS)}")
    links = [item for item in parsed if item.kind == "soundcloud_track"]
    assert len(links) == MAX_ITEMS
    assert TOO_LONG not in parsed


def test_only_the_first_plain_words_are_listed() -> None:
    parsed = parse_inputs(f"{numbered_words(MAX_WORDS + 50)} https://soundcloud.com/a/b")
    rejects = [item.raw for item in parsed if item.kind == "unsupported"]
    assert rejects == [f"word{number}" for number in range(MAX_WORDS)]
    assert parsed[-1].url == "https://soundcloud.com/a/b"


def test_rejected_links_and_missing_files_are_all_listed() -> None:
    playlists = [f"https://www.youtube.com/playlist?list=PL{number}" for number in range(150)]
    missing = [f"/no-such-folder/song-{number}.mp3" for number in range(150)]
    parsed = parse_inputs(" ".join(playlists + missing))
    assert [item.raw for item in parsed] == playlists + missing
    assert {item.reason for item in parsed} == {YOUTUBE_PLAYLIST, FILE_NOT_FOUND}


def test_missing_files_count_toward_the_item_limit() -> None:
    parsed = parse_inputs(" ".join(f"/no-such-folder/song-{n}.mp3" for n in range(MAX_ITEMS + 5)))
    assert len(parsed) == MAX_ITEMS + 1
    assert parsed[-1] == TOO_LONG


def test_text_past_the_character_limit_is_skipped_without_cutting_an_item() -> None:
    filler = "x" * (MAX_CHARACTERS - 10)
    # The limit falls inside the link, so the link isn't read at all.
    parsed = parse_inputs(f"{filler} https://soundcloud.com/artist/track")
    assert parsed == [ParsedInput("unsupported", filler, reason=NOT_SUPPORTED), TOO_LONG]


def test_whitespace_past_the_character_limit_isnt_reported() -> None:
    text = "https://soundcloud.com/artist/track" + " " * MAX_CHARACTERS
    assert TOO_LONG not in parse_inputs(text)


def test_reasons_for_the_limits_name_the_numbers() -> None:
    assert "2,000 links and files" in PASTE_TOO_LONG
    assert "1,000,000 characters" in PASTE_TOO_LONG


def test_a_quoted_link_keeps_its_quotes_in_raw() -> None:
    assert parse_inputs('"https://soundcloud.com/artist/track"') == [
        ParsedInput(
            "soundcloud_track",
            '"https://soundcloud.com/artist/track"',
            url="https://soundcloud.com/artist/track",
        )
    ]


@pytest.fixture
def music(tmp_path: Path) -> Path:
    """A folder with spaces in its name, holding audio files and one that isn't audio."""
    folder = tmp_path / "My Music"
    folder.mkdir()
    for name in ("Some Song.mp3", "Other Song.M4A", "Notes.wav"):
        (folder / name).write_bytes(b"not really audio")
    return folder


def local(raw: str, path: Path) -> ParsedInput:
    return ParsedInput("local_file", raw, path=path)


def test_a_path_with_spaces_on_its_own_line_is_one_file(music: Path) -> None:
    song = music / "Some Song.mp3"
    assert parse_inputs(str(song)) == [local(str(song), song)]


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quotes_from_drag_and_drop_are_removed(music: Path, quote: str) -> None:
    song = music / "Some Song.mp3"
    raw = f"{quote}{song}{quote}"
    assert parse_inputs(raw) == [local(raw, song)]


def test_quoted_paths_with_spaces_on_one_line_are_separate_files(music: Path) -> None:
    first, second = music / "Some Song.mp3", music / "Other Song.M4A"
    text = f'"{first}" "{second}" https://youtu.be/dQw4w9WgXcQ'
    assert parse_inputs(text) == [
        local(f'"{first}"', first),
        local(f'"{second}"', second),
        ParsedInput("youtube_video", "https://youtu.be/dQw4w9WgXcQ", url=VIDEO),
    ]


def test_paths_and_links_on_separate_lines(music: Path) -> None:
    song = music / "Some Song.mp3"
    text = f"https://soundcloud.com/artist/track\n{song}\r\n"
    assert [parsed.kind for parsed in parse_inputs(text)] == ["soundcloud_track", "local_file"]


def test_a_file_uri_is_a_local_file(music: Path) -> None:
    song = music / "Some Song.mp3"
    uri = song.as_uri()
    assert "%20" in uri
    assert parse_inputs(uri) == [local(uri, song)]


def test_a_file_uri_with_an_uppercase_scheme_is_a_local_file(music: Path) -> None:
    song = music / "Some Song.mp3"
    uri = "FILE" + song.as_uri().removeprefix("file")
    assert classify(uri) == local(uri, song)


@pytest.mark.skipif(os.name != "nt", reason="drive letters only exist on Windows")
def test_a_file_uri_with_a_drive_letter_is_a_local_file(music: Path) -> None:
    song = music / "Some Song.mp3"
    uri = "file:///" + song.as_posix().replace(" ", "%20")
    assert uri.startswith(f"file:///{song.drive}/")
    assert parse_inputs(uri) == [local(uri, song)]


def test_a_windows_drive_uri_that_isnt_there_is_not_found() -> None:
    # On macOS and Linux the URI can't be a path at all; on Windows the drive is missing.
    uri = "file:///Q:/sc-mp3%20test/missing%20song.mp3"
    assert classify(uri) == ParsedInput("unsupported", uri, reason=FILE_NOT_FOUND)


def test_a_windows_path_that_isnt_there_is_not_found() -> None:
    raw = '"Q:\\sc-mp3 test\\missing song.mp3"'
    assert parse_inputs(raw) == [ParsedInput("unsupported", raw, reason=FILE_NOT_FOUND)]


def test_the_same_file_written_three_ways_is_one_input(music: Path) -> None:
    song = music / "Some Song.mp3"
    text = f'{song}\n"{song}"\n{song.as_uri()}'
    assert parse_inputs(text) == [local(str(song), song)]


def test_an_uppercase_extension_is_accepted(music: Path) -> None:
    song = music / "Other Song.M4A"
    assert classify(str(song)) == local(str(song), song)


def test_a_relative_path_is_made_absolute(music: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(music)
    assert classify("Some Song.mp3") == local("Some Song.mp3", music / "Some Song.mp3")


def test_a_home_relative_path_is_expanded(music: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(music.parent))
    monkeypatch.setenv("USERPROFILE", str(music.parent))
    raw = "~/My Music/Some Song.mp3"
    assert parse_inputs(raw) == [local(raw, music / "Some Song.mp3")]


def test_a_file_that_isnt_mp3_or_m4a_is_unsupported(music: Path) -> None:
    notes = str(music / "Notes.wav")
    assert classify(notes) == ParsedInput("unsupported", notes, reason=NOT_RETAGGABLE)


def test_a_missing_audio_file_is_not_found(music: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = str(music / "Missing.mp3")
    assert classify(missing) == ParsedInput("unsupported", missing, reason=FILE_NOT_FOUND)
    monkeypatch.chdir(music)
    assert classify("Missing.mp3") == ParsedInput(
        "unsupported", "Missing.mp3", reason=FILE_NOT_FOUND
    )


def test_a_folder_is_unsupported(music: Path) -> None:
    assert parse_inputs(str(music)) == [
        ParsedInput("unsupported", str(music), reason=FOLDER_NOT_FILE)
    ]


def test_non_ascii_names_work_bare_quoted_and_as_a_uri(tmp_path: Path) -> None:
    folder = tmp_path / "Música con espacios"
    folder.mkdir()
    song = folder / "Canción \u2013 Ñandú.mp3"
    song.write_bytes(b"not really audio")
    for raw in (str(song), f'"{song}"', song.as_uri()):
        assert parse_inputs(raw) == [local(raw, song)]


@pytest.mark.parametrize(
    "relative",
    [
        "from soundcloud.com stuff/My Song.mp3",
        "Song from youtube.com (live).mp3",
        "Rip - soundcloud.com - Track.mp3",
        "Rips from youtube.com/My Song.mp3",
        "Mixes on soundcloud.com/My Song.mp3",
    ],
)
def test_a_file_whose_path_names_a_site_is_still_one_file(tmp_path: Path, relative: str) -> None:
    song = tmp_path / relative
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"not really audio")
    for raw in (str(song), f'"{song}"', song.as_posix()):
        assert parse_inputs(raw) == [local(raw, song)]


# The no_share_lookups fixture fails these if anything checks the share on disk.
@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows thing")
def test_a_share_path_to_audio_is_accepted_without_looking_at_it() -> None:
    for raw in ("\\\\nas\\music\\song.mp3", "\\\\nas\\My Music\\Some Song.M4A"):
        assert parse_inputs(raw) == [local(raw, Path(raw))]
    uri = "file://nas/My%20Music/song.mp3"
    assert parse_inputs(uri) == [local(uri, Path("\\\\nas\\My Music\\song.mp3"))]


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows thing")
def test_a_share_path_that_isnt_audio_is_rejected_without_looking_at_it() -> None:
    for raw in ("\\\\nas\\music\\notes.wav", "\\\\nas\\music", "\\\\192.0.2.4\\a"):
        assert parse_inputs(raw) == [ParsedInput("unsupported", raw, reason=NOT_RETAGGABLE)]


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows thing")
def test_share_paths_split_from_the_links_beside_them() -> None:
    first, second, third = (f"\\\\nas\\music\\{name}.mp3" for name in "abc")
    text = f'"{first}" "{second}"\n{third} https://soundcloud.com/artist/track'
    assert [(parsed.kind, parsed.raw) for parsed in parse_inputs(text)] == [
        ("local_file", f'"{first}"'),
        ("local_file", f'"{second}"'),
        ("local_file", third),
        ("soundcloud_track", "https://soundcloud.com/artist/track"),
    ]


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows thing")
@pytest.mark.parametrize(
    "template",
    [
        pytest.param("{a} {b}", id="a space"),
        pytest.param("{a}\t{b}", id="a tab"),
        pytest.param("{a}  \t {b}", id="mixed whitespace"),
        pytest.param("'{a}' '{b}'", id="single quotes"),
        pytest.param('"{a}"\t"{b}"', id="double quotes and a tab"),
    ],
)
def test_two_share_paths_on_one_line_stay_two_files(template: str) -> None:
    first, second = "\\\\nas\\music\\a.mp3", "\\\\nas\\music\\b.mp3"
    paths = [parsed.path for parsed in parse_inputs(template.format(a=first, b=second))]
    assert paths == [Path(first), Path(second)]


@pytest.mark.skipif(os.name != "nt", reason="mapped drives are a Windows thing")
def test_a_mapped_network_drive_is_handled_like_a_share() -> None:
    song, notes = f"{REMOTE_DRIVE}\\My Music\\Some Song.mp3", f"{REMOTE_DRIVE}\\notes.wav"
    assert parse_inputs(f"{song}\n{notes}") == [
        local(song, Path(song)),
        ParsedInput("unsupported", notes, reason=NOT_RETAGGABLE),
    ]


@pytest.mark.skipif(os.name == "nt", reason="on Windows /net is a folder on the current drive")
def test_paths_under_net_are_handled_like_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    # The automounter root only exists on macOS, so the test turns it on everywhere else.
    monkeypatch.setattr(inputs, "_NETWORK_ROOTS", ("/net/",))
    song, notes = "/net/nas/My Music/Some Song.mp3", "/net/nas/notes.wav"
    assert parse_inputs(f"{song}\n{notes}") == [
        local(song, Path(song)),
        ParsedInput("unsupported", notes, reason=NOT_RETAGGABLE),
    ]


@pytest.mark.skipif(os.name == "nt", reason="on Windows this is a share")
def test_a_share_path_on_macos_and_linux_is_not_found() -> None:
    raw = "\\\\nas\\music\\song.mp3"
    assert parse_inputs(raw) == [ParsedInput("unsupported", raw, reason=FILE_NOT_FOUND)]
