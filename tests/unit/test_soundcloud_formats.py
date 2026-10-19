"""Tests for choosing the SoundCloud format to download, by rules 1 to 4 of TECH_SPEC §6."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Availability, FormatChoice, FormatOption, SourceInfo
from scmp3.core.sources import soundcloud, soundcloud_formats


def load_record_module() -> ModuleType:
    """tests/fixtures/record.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_record"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "record.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


record = load_record_module()

# The errors that fixtures which can't be saved give instead of a format.
REFUSALS = {ErrorKind.preview_only, ErrorKind.drm, ErrorKind.unavailable}


def fixture_source(name: str) -> SourceInfo:
    fixture = record.load(name)
    return soundcloud.resolve(fixture["url"], record.ReplayYdl(fixture))


def choose(name: str, *, signed_in: bool = False) -> FormatChoice:
    return soundcloud_formats.choose_format(fixture_source(name), signed_in=signed_in)


def source_with(*formats: FormatOption, availability: Availability = "ok") -> SourceInfo:
    return SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/a/b",
        title="b",
        uploader="a",
        availability=availability,
        formats=list(formats),
    )


def stream(format_id: str, bitrate: float, *, preview: bool = False) -> FormatOption:
    codec = format_id.split("_")[1]
    ext = {"aac": "m4a"}.get(codec, codec)
    return FormatOption(format_id, ext, codec=codec, bitrate=bitrate, is_preview=preview)


def original(ext: str, codec: str | None = None) -> FormatOption:
    return FormatOption("download", ext, codec=codec, is_original=True)


def chosen_ids(choice: FormatChoice) -> list[str]:
    return [fmt.format_id for fmt in (choice.format, choice.fallback) if fmt is not None]


@pytest.mark.parametrize("name", record.names())
@pytest.mark.parametrize("signed_in", [False, True])
def test_no_fixture_ever_gets_a_preview_or_hls_aes(name: str, signed_in: bool) -> None:
    try:
        choice = choose(name, signed_in=signed_in)
    except ScmpError as error:
        assert error.kind in REFUSALS
        return
    for format_id in chosen_ids(choice):
        assert "_preview" not in format_id
        assert not format_id.startswith("hls-aes")


def test_a_typical_track_gets_aac_160k() -> None:
    choice = choose("aac_typical")
    assert choice.format.format_id == "hls_aac_160k"
    assert (choice.needs_bitrate_check, choice.fallback) == (False, None)


def test_go_plus_gets_aac_256k_over_160k_and_mp3() -> None:
    assert choose("go_plus", signed_in=True).format.format_id == "hls_aac_256k"


def test_a_legacy_track_gets_its_progressive_mp3() -> None:
    choice = choose("legacy_mp3")
    assert choice.format.format_id == "http_mp3_0_0"
    assert choice.needs_bitrate_check is False


def test_a_signed_in_account_keeps_a_lossless_original() -> None:
    choice = choose("original_wav", signed_in=True)
    assert choice.format.format_id == "download"
    assert choice.format.ext == "wav"
    assert choice.needs_bitrate_check is False
    assert choice.fallback is not None
    assert choice.fallback.format_id == "hls_aac_160k"


def test_a_lossy_original_needs_a_bitrate_check_against_the_best_stream() -> None:
    choice = choose("original_mp3", signed_in=True)
    assert choice.format.format_id == "download"
    assert choice.needs_bitrate_check is True
    assert choice.fallback is not None
    assert choice.fallback.format_id == "hls_aac_160k"


@pytest.mark.parametrize("name", ["original_wav", "original_mp3"])
def test_originals_are_ignored_when_not_signed_in(name: str) -> None:
    assert choose(name, signed_in=False).format.format_id == "hls_aac_160k"


@pytest.mark.parametrize(
    ("name", "kind"),
    [("preview_only", ErrorKind.preview_only), ("drm_only", ErrorKind.drm)],
)
def test_tracks_that_cant_be_saved_raise_their_kind(name: str, kind: ErrorKind) -> None:
    with pytest.raises(ScmpError) as error_info:
        choose(name, signed_in=True)
    assert error_info.value.kind is kind


@pytest.mark.parametrize(
    ("availability", "kind"),
    [
        ("preview_only", ErrorKind.preview_only),
        ("drm", ErrorKind.drm),
        ("private", ErrorKind.unavailable),
        ("geo_blocked", ErrorKind.geo_blocked),
    ],
)
def test_every_availability_but_ok_raises(availability: Availability, kind: ErrorKind) -> None:
    source = source_with(stream("hls_aac_160k", 160), availability=availability)
    with pytest.raises(ScmpError) as error_info:
        soundcloud_formats.choose_format(source, signed_in=True)
    assert error_info.value.kind is kind


@pytest.mark.parametrize(
    ("ext", "codec", "lossless"),
    [
        ("wav", None, True),
        ("flac", None, True),
        ("aiff", None, True),
        ("aif", None, True),
        ("WAV", None, True),
        ("m4a", "alac", True),
        ("m4a", None, False),
        ("m4a", "aac", False),
        ("mp3", None, False),
        ("ogg", None, False),
    ],
)
def test_only_lossless_originals_skip_the_bitrate_check(
    ext: str, codec: str | None, lossless: bool
) -> None:
    source = source_with(stream("hls_aac_160k", 160), original(ext, codec))
    choice = soundcloud_formats.choose_format(source, signed_in=True)
    assert choice.format.is_original
    assert choice.needs_bitrate_check is not lossless


def test_a_lossy_original_with_no_stream_has_no_fallback() -> None:
    choice = soundcloud_formats.choose_format(source_with(original("mp3")), signed_in=True)
    assert choice == FormatChoice(original("mp3"), needs_bitrate_check=True, fallback=None)


def test_hls_aes_is_never_chosen_even_at_a_higher_bitrate() -> None:
    source = source_with(stream("hls-aes_aac_256k", 256), stream("hls_aac_160k", 160))
    assert (
        soundcloud_formats.choose_format(source, signed_in=True).format.format_id == "hls_aac_160k"
    )


def test_a_preview_is_never_chosen_even_without_its_flag() -> None:
    unflagged = stream("hls_aac_160k_preview", 160)
    source = source_with(unflagged, stream("hls_aac_96k", 96))
    assert (
        soundcloud_formats.choose_format(source, signed_in=False).format.format_id == "hls_aac_96k"
    )


def test_a_flagged_preview_is_never_chosen_whatever_its_name() -> None:
    source = source_with(stream("hls_aac_160k", 160, preview=True), stream("hls_aac_96k", 96))
    assert (
        soundcloud_formats.choose_format(source, signed_in=False).format.format_id == "hls_aac_96k"
    )


def test_any_aac_beats_legacy_mp3() -> None:
    source = source_with(stream("http_mp3_0_0", 128), stream("hls_aac_96k", 96))
    assert (
        soundcloud_formats.choose_format(source, signed_in=False).format.format_id == "hls_aac_96k"
    )


def test_the_higher_aac_bitrate_wins_whatever_the_protocol() -> None:
    source = source_with(stream("http_aac_96k", 96), stream("hls_aac_160k", 160))
    assert (
        soundcloud_formats.choose_format(source, signed_in=False).format.format_id == "hls_aac_160k"
    )


def test_a_progressive_file_wins_a_tie_with_hls() -> None:
    source = source_with(stream("hls_mp3_0_0", 128), stream("http_mp3_0_0", 128))
    assert (
        soundcloud_formats.choose_format(source, signed_in=False).format.format_id == "http_mp3_0_0"
    )


def test_without_an_aac_or_mp3_stream_the_engine_needs_an_update() -> None:
    source = source_with(stream("hls_opus_64k", 64))
    with pytest.raises(ScmpError) as error_info:
        soundcloud_formats.choose_format(source, signed_in=False)
    assert error_info.value.kind is ErrorKind.engine_outdated
