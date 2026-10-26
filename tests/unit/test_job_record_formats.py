"""Tests for the format a job shows: the source's own choice before the download, then what was
downloaded. YouTube's choice follows the audio format setting."""

from pathlib import Path

from scmp3.app.job_record import JobRecord
from scmp3.app.job_views import format_choice, job_view
from scmp3.core.convert import AudioInfo
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import FormatOption, Job, Settings, SourceInfo
from scmp3.core.pipeline import Prepared, Staged, Tools
from scmp3.core.suggest import suggest

URL = "https://www.youtube.com/watch?v=y9qO_xgE34Q"
OPUS = FormatOption("251", "webm", "opus", 142.6)
AAC = FormatOption("140", "m4a", "aac", 129.5)


def youtube_record(tmp_path: Path) -> JobRecord:
    source = SourceInfo(
        kind="youtube",
        id="youtube:y9qO_xgE34Q",
        url=URL,
        title="WAIT FOR U",
        uploader="Future - Topic",
        artists=["Future"],
        formats=[AAC, OPUS],
    )
    settings = Settings(library_dir=tmp_path / "library")
    prepared = Prepared(
        job_id="job",
        source=source,
        suggestion=suggest(source, settings),
        duplicate=None,
        settings=settings,
        info={},
        tools=Tools(history=History(tmp_path / "history.db")),
    )
    job = Job(id="job", kind="track", input=URL, source=source, suggestion=prepared.suggestion)
    return JobRecord(job=job, parsed=classify(URL), prepared=prepared)


def test_a_youtube_job_shows_the_stream_its_audio_format_takes(tmp_path: Path) -> None:
    record = youtube_record(tmp_path)
    mp3 = Settings(library_dir=tmp_path / "library")
    m4a = Settings(library_dir=tmp_path / "library", audio_format="m4a")
    assert (choice := format_choice(record, mp3)) is not None and choice.format == OPUS
    assert (choice := format_choice(record, m4a)) is not None and choice.format == AAC
    assert job_view(record, m4a)["filename"] == "Future/Future - WAIT FOR U.m4a"


def test_once_downloaded_a_job_shows_what_was_downloaded(tmp_path: Path) -> None:
    record = youtube_record(tmp_path)
    audio = AudioInfo(duration=190.0, bitrate=320, codec="mp3", sample_rate=48000)
    record.staged = Staged(tmp_path / "audio.mp3", "mp3", audio, AAC)
    choice = format_choice(record, Settings(library_dir=tmp_path / "library"))
    assert choice is not None and choice.format == AAC
