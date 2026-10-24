"""Tests for the YouTube stream choice: Opus 251 for MP3, AAC 140 to keep as .m4a."""

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import AudioFormat, FormatOption, SourceInfo
from scmp3.core.sources import youtube_formats

OPUS = FormatOption("251", "webm", "opus", 142.6)
AAC = FormatOption("140", "m4a", "aac", 129.5)
LOW_OPUS = FormatOption("250", "webm", "opus", 72.9)
LOW_AAC = FormatOption("139", "m4a", "aac", 48.8)


def video(*formats: FormatOption) -> SourceInfo:
    return SourceInfo(
        kind="youtube",
        id="youtube:y9qO_xgE34Q",
        url="https://www.youtube.com/watch?v=y9qO_xgE34Q",
        title="WAIT FOR U",
        uploader="Future",
        formats=list(formats),
    )


@pytest.mark.parametrize(
    ("audio_format", "chosen"),
    [("mp3_320", OPUS), ("mp3_v0", OPUS), ("m4a", AAC)],
)
def test_each_audio_format_takes_its_stream(
    audio_format: AudioFormat, chosen: FormatOption
) -> None:
    source = video(LOW_AAC, LOW_OPUS, AAC, OPUS)
    choice = youtube_formats.choose_format(source, audio_format=audio_format)
    assert choice.format == chosen
    assert (choice.needs_bitrate_check, choice.fallback) == (False, None)


@pytest.mark.parametrize(
    ("audio_format", "only", "chosen"),
    [("mp3_320", AAC, AAC), ("m4a", OPUS, OPUS)],
)
def test_the_other_stream_stands_in_when_one_is_missing(
    audio_format: AudioFormat, only: FormatOption, chosen: FormatOption
) -> None:
    source = video(LOW_OPUS, only)
    assert youtube_formats.choose_format(source, audio_format=audio_format).format == chosen


def test_a_video_without_either_stream_says_so_rather_than_saving_thin_audio() -> None:
    with pytest.raises(ScmpError) as error:
        youtube_formats.choose_format(video(LOW_AAC, LOW_OPUS), audio_format="mp3_320")
    assert error.value.kind == ErrorKind.engine_outdated


def test_of_two_copies_of_one_stream_the_higher_bitrate_wins() -> None:
    copy = FormatOption("251-1", "webm", "opus", 150.0)
    assert youtube_formats.choose_format(video(OPUS, copy), audio_format="mp3_320").format == copy


@pytest.mark.parametrize(
    ("format_id", "itag"), [("251", "251"), ("251-22", "251"), ("140-0", "140")]
)
def test_the_itag_is_the_start_of_the_format_id(format_id: str, itag: str) -> None:
    assert youtube_formats.itag(FormatOption(format_id, "webm")) == itag
