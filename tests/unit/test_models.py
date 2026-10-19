"""Tests for the data model, the error kinds, and the JSON round trip every model supports."""

import json
import logging
import pickle
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import (
    Account,
    ArtworkChoice,
    Change,
    CropBox,
    FormatChoice,
    FormatOption,
    HistoryEntry,
    Job,
    JobError,
    JobState,
    Model,
    OriginalDownload,
    PlaylistEntry,
    PlaylistInfo,
    Progress,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    account_tier,
)

AAC_160 = FormatOption(format_id="hls_aac_160k", ext="m4a", codec="aac", bitrate=160.0)
ORIGINAL_MP3 = FormatOption(
    format_id="download", ext="mp3", codec="mp3", bitrate=128.0, is_original=True
)
PREVIEW = FormatOption(format_id="hls_aac_160k_preview", ext="m4a", codec="aac", is_preview=True)

PLAYLIST = PlaylistInfo(
    title="Lantern",
    owner="Mara Lune",
    set_type="album",
    is_album=True,
    artwork_url="https://i1.sndcdn.com/artworks-lantern-original.jpg",
    entries=[
        PlaylistEntry(url="https://soundcloud.com/mara-lune/one", id="111", title="One"),
        PlaylistEntry(url="https://soundcloud.com/mara-lune/two"),
    ],
)

# Every field set, with unicode, dates, nested models and lists.
SOURCE = SourceInfo(
    kind="soundcloud",
    id="soundcloud:tracks:123",
    url="https://soundcloud.com/kaia/midnight-drive",
    title="Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
    uploader="Kaia",
    artists=["Kaia", "Lou V"],
    album="Nachtfahrt: Édition 夜",
    release_date=date(2026, 3, 14),
    upload_date=date(2026, 3, 15),
    genre="Hip-hop",
    description="Out now on Night Records.\nFree download: link in bio",
    duration=221.5,
    full_duration=221.5,
    artwork_url="https://i1.sndcdn.com/artworks-midnight-original.jpg",
    artwork_is_avatar=True,
    isrc="QZABC2600001",
    label="Night Records",
    explicit=False,
    availability="preview_only",
    original=OriginalDownload(ext="wav", needs_sign_in=True),
    formats=[AAC_160, ORIGINAL_MP3, PREVIEW],
    playlist=PLAYLIST,
)

CHANGE = Change(
    id="c1",
    kind="remove_producer",
    span="[prod. Hazy]",
    label='Removed "[prod. Hazy]"',
    enabled=False,
)

SUGGESTION = TagSuggestion(
    base_title="Midnight Drive",
    main_artists=["Kaia"],
    featured=["Lou V"],
    version=" (VIP Mix)",
    album="Midnight Drive",
    album_artist="Kaia",
    year=2026,
    genre="Hip-hop",
    track_no=1,
    track_total=5,
    changes=[CHANGE, Change(id="c2", kind="remove_promo", span="FREE DL", label="Removed")],
)

TAGS = Tags(
    title="Midnight Drive (VIP Mix)",
    artist="Kaia, Lou V",
    album="Midnight Drive",
    album_artist="Kaia",
    year=2026,
    genre="Hip-hop",
    track_no=1,
    track_total=5,
    comment="https://soundcloud.com/kaia/midnight-drive",
    source_url="https://soundcloud.com/kaia/midnight-drive",
    source_id="soundcloud:tracks:123",
    isrc="QZABC2600001",
    label="Night Records",
)

CROP = CropBox(left=0.125, top=0.0, width=0.75, height=1.0)
ARTWORK = ArtworkChoice(kind="file", value=str(Path("covers") / "moon.png"), crop=CROP)
PROGRESS = Progress(pct=62.5, speed=1_250_000.0, eta=4.0, downloaded=3_100_000, total=4_960_000)
ERROR = JobError(kind=ErrorKind.convert_failed, detail="ffmpeg exited with code 1")

HISTORY_ENTRY = HistoryEntry(
    source_id="soundcloud:tracks:123",
    url_norm="https://soundcloud.com/kaia/midnight-drive",
    saved_path=Path("Music") / "sc-mp3" / "Kaia - Midnight Drive.mp3",
    title="Midnight Drive",
    artist="Kaia",
    album="Midnight Drive",
    format="mp3",
    bitrate=320,
    saved_at=datetime(2026, 10, 3, 18, 30, 5, 123456, tzinfo=UTC),
    id=7,
)
CHILD = Job(id="j2", kind="track", input="https://soundcloud.com/mara-lune/one", included=False)
JOB = Job(
    id="j1",
    kind="set",
    input="https://soundcloud.com/kaia/midnight-drive?si=abc",
    state=JobState.needs_review,
    source=SOURCE,
    suggestion=SUGGESTION,
    overrides={"title": "Midnight Drive", "album": "Nächte"},
    style="feat_in_title",
    artwork=ARTWORK,
    staged_audio=Path("staging") / "j1" / "source.m4a",
    output_path=Path("Music") / "sc-mp3" / "Kaia - Midnight Drive.mp3",
    error=ERROR,
    progress=PROGRESS,
    duplicate=HISTORY_ENTRY,
    children=[CHILD, Job(id="j3", kind="track", input="x", state=JobState.skipped)],
    album_mode="singles",
)

ACCOUNT = Account(
    username="Kaia",
    permalink="kaia",
    avatar_url="https://i1.sndcdn.com/avatars-kaia.jpg",
    tier="go_plus",
)
SETTINGS = Settings(
    library_dir=Path("Music") / "Spotify songs",
    filename_template="{title}",
    when_file_exists="keep_both",
    album_subfolders=True,
    title_style="custom",
    custom_title_template="{title} [{featured}]{version}",
    custom_artist_template="{main}",
    artist_separator=" x ",
    feat_wording="featuring",
    remove_producer_credits=False,
    remove_promo_text=False,
    custom_words=["NOW PLAYING", "Bootleg Club"],
    singles_album="fixed",
    singles_album_text="Singles",
    audio_format="m4a",
    keep_lossless_originals=True,
    cover_max_size=640,
    save_cover_jpg=True,
    comment_mode="description",
    review_before_saving=False,
    download_while_reviewing=False,
    offer_clipboard_links=False,
    concurrent_downloads=4,
    check_app_updates=False,
    check_engine_updates=False,
    engine_channel="nightly",
    soundcloud_client_id="a1b2c3",
    first_run_done=True,
)

# One instance per model with every field filled in.
FULL_SAMPLES: list[Model] = [
    OriginalDownload(ext="flac"),
    AAC_160,
    FormatChoice(format=ORIGINAL_MP3, needs_bitrate_check=True, fallback=AAC_160),
    PLAYLIST.entries[0],
    PLAYLIST,
    SOURCE,
    CHANGE,
    SUGGESTION,
    TAGS,
    CROP,
    ARTWORK,
    PROGRESS,
    ERROR,
    JOB,
    ACCOUNT,
    HISTORY_ENTRY,
    SETTINGS,
]

# The same models with only their required fields, so None and the defaults round-trip too.
MINIMAL_SAMPLES: list[Model] = [
    OriginalDownload(),
    FormatOption(format_id="http_mp3_1_0", ext="mp3"),
    FormatChoice(format=AAC_160),
    PlaylistEntry(url="https://soundcloud.com/teo-marr/night-bus"),
    PlaylistInfo(title="Mix", owner="Teo Marr"),
    SourceInfo(kind="file", id="old-favorite.mp3", url="old-favorite.mp3", title="", uploader=""),
    Change(id="c", kind="fix_caps", span="LOUD", label="Fixed caps"),
    TagSuggestion(base_title="", main_artists=[]),
    Tags(title="T", artist="A", album="", album_artist=""),
    ArtworkChoice(),
    Progress(),
    JobError(kind=ErrorKind.offline),
    Job(id="j", kind="retag", input="old-favorite.mp3"),
    Account(username="u", permalink="u"),
    HistoryEntry(None, None, Path("x.mp3"), "T", "A", "", "mp3"),
    Settings(),
]


def model_classes() -> set[type[Model]]:
    """Every subclass of Model, however deep."""
    found: set[type[Model]] = set()
    pending = [Model]
    while pending:
        for subclass in pending.pop().__subclasses__():
            found.add(subclass)
            pending.append(subclass)
    return found


def test_every_model_has_a_full_sample() -> None:
    assert {type(sample) for sample in FULL_SAMPLES} == model_classes()


@pytest.mark.parametrize(
    "sample", FULL_SAMPLES + MINIMAL_SAMPLES, ids=lambda sample: type(sample).__name__
)
def test_every_model_round_trips_through_json(sample: Model) -> None:
    # json.dumps fails on anything that isn't JSON-safe.
    data = json.loads(json.dumps(sample.to_dict()))
    assert type(sample).from_dict(data) == sample


def test_to_dict_writes_dates_as_iso_and_paths_and_enums_as_strings() -> None:
    data = JOB.to_dict()
    assert data["state"] == "needs_review"
    assert data["error"]["kind"] == "convert_failed"
    assert data["source"]["release_date"] == "2026-03-14"
    assert data["staged_audio"] == str(Path("staging") / "j1" / "source.m4a")
    assert HISTORY_ENTRY.to_dict()["saved_at"] == "2026-10-03T18:30:05.123456+00:00"


def test_from_dict_rebuilds_nested_models_and_types() -> None:
    job = Job.from_dict(json.loads(json.dumps(JOB.to_dict())))
    assert job.state is JobState.needs_review
    assert isinstance(job.staged_audio, Path)
    assert isinstance(job.source, SourceInfo)
    assert isinstance(job.source.release_date, date)
    assert isinstance(job.source.playlist, PlaylistInfo)
    assert all(isinstance(child, Job) for child in job.children)
    assert job.error is not None
    assert job.error.kind is ErrorKind.convert_failed


def test_from_dict_uses_defaults_for_missing_keys_and_ignores_unknown_ones() -> None:
    job = Job.from_dict({"id": "j1", "kind": "track", "input": "x", "added_later": True})
    assert job == Job(id="j1", kind="track", input="x")


def test_a_new_job_takes_the_sources_own_cover_and_is_included() -> None:
    job = Job(id="j1", kind="track", input="x")
    assert job.artwork == ArtworkChoice(kind="source")
    assert job.included is True
    assert job.album_mode is None
    assert job.duplicate is None


def test_a_duplicate_keeps_the_earlier_saves_path_and_date() -> None:
    data = json.loads(json.dumps(JOB.to_dict()))
    assert data["duplicate"]["saved_path"] == str(HISTORY_ENTRY.saved_path)
    assert data["duplicate"]["saved_at"] == "2026-10-03T18:30:05.123456+00:00"
    assert Job.from_dict(data).duplicate == HISTORY_ENTRY


@pytest.mark.parametrize(
    ("product_id", "tier"),
    [
        ("free", "free"),
        ("consumer-free", "free"),
        ("consumer-mid-tier", "go"),
        ("consumer-high-tier", "go_plus"),
        ("consumer-high-dj-tier", "dj"),
    ],
)
def test_account_tier_maps_soundcloud_product_ids(product_id: str, tier: str) -> None:
    assert account_tier(product_id) == tier


@pytest.mark.parametrize("product_id", ["consumer-ultra-tier", "", None])
def test_an_unknown_product_id_counts_as_free_and_is_logged(
    product_id: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="scmp3.core.models"):
        assert account_tier(product_id) == "free"
    assert repr(product_id) in caplog.text


def test_a_new_account_counts_as_free() -> None:
    assert Account(username="u", permalink="u").tier == "free"


def test_from_dict_turns_whole_numbers_into_floats_for_float_fields() -> None:
    progress = Progress.from_dict({"pct": 50, "speed": 1000})
    assert progress == Progress(pct=50.0, speed=1000.0)
    assert isinstance(progress.pct, float)


def test_mutable_defaults_are_not_shared() -> None:
    first = Job(id="a", kind="track", input="x")
    second = Job(id="b", kind="track", input="y")
    first.children.append(second)
    assert second.children == []


def test_error_kinds_are_exactly_the_spec_list() -> None:
    assert [kind.value for kind in ErrorKind] == [
        "drm",
        "preview_only",
        "unavailable",
        "geo_blocked",
        "rate_limited",
        "auth_expired",
        "engine_outdated",
        "bot_check",
        "offline",
        "convert_failed",
        "disk",
        "duplicate",
        "service_down",
        "bad_download",
        "internal",
    ]


def test_job_states_cover_the_job_lifecycle() -> None:
    assert [state.value for state in JobState] == [
        "queued",
        "resolving",
        "needs_review",
        "saving",
        "saved",
        "skipped",
        "error",
    ]


def test_scmp_error_carries_its_kind_and_detail() -> None:
    error = ScmpError(ErrorKind.convert_failed, "ffmpeg not found")
    assert error.kind is ErrorKind.convert_failed
    assert error.detail == "ffmpeg not found"
    assert str(error) == "convert_failed: ffmpeg not found"


def test_scmp_error_without_detail_reads_as_its_kind() -> None:
    error = ScmpError(ErrorKind.offline)
    assert error.detail is None
    assert str(error) == "offline"


def test_scmp_error_accepts_a_kind_name_and_rejects_unknown_ones() -> None:
    assert ScmpError("drm").kind is ErrorKind.drm
    with pytest.raises(ValueError):
        ScmpError("oops")


def test_scmp_error_survives_pickling() -> None:
    error = pickle.loads(pickle.dumps(ScmpError(ErrorKind.disk, "full")))
    assert (error.kind, error.detail) == (ErrorKind.disk, "full")
