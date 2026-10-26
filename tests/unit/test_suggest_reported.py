"""The track reported from v0.1, and the artist lists, faces and emoji it showed, end to end."""

import json
from datetime import date
from pathlib import Path

import pytest

from scmp3.core import library
from scmp3.core.filenames import utf16_units
from scmp3.core.library import plan_destination
from scmp3.core.models import Settings, SourceInfo
from scmp3.core.suggest import render, suggest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "suggest" / "gg_fuckin_ez.json"
FACE_TITLE = "gg fuckin ez \u0d26\u0d4d\u0d26\u0d3f(\u02f5 \u2022\u0300 \u1d17 - \u02f5 ) \u2727"
# Twenty made-up names, the uploader's first.
TWENTY = [f"name{number:02d} \U0001f525" for number in range(1, 21)]


def reported() -> SourceInfo:
    """The reported track, from its fixture."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return SourceInfo(
        kind=data["kind"],
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title=data["title"],
        uploader=data["uploader"],
        artists=data["artists"],
        release_date=date.fromisoformat(data["release_date"]),
        upload_date=date.fromisoformat(data["upload_date"]),
    )


def track(title: str, uploader: str, artists: list[str]) -> SourceInfo:
    """A SoundCloud track with these names."""
    return SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:2",
        url="https://soundcloud.com/kaia/paper-moon",
        title=title,
        uploader=uploader,
        artists=artists,
    )


def test_the_reported_track_keeps_its_face_and_credits_gunk_with_two_features() -> None:
    settings = Settings()
    suggestion = suggest(reported(), settings)
    tags = render(suggestion, "artist_field", {}, settings)

    assert suggestion.base_title == FACE_TITLE
    assert (suggestion.main_artists, suggestion.featured) == (["gunk"], ["zayok", "edyn"])
    assert [change.id for change in suggestion.changes] == []
    assert (tags.title, tags.artist, tags.album_artist) == (
        FACE_TITLE,
        "gunk, zayok, edyn",
        "gunk",
    )


def test_the_reported_track_with_feat_in_title_ends_in_both_features() -> None:
    settings = Settings()
    tags = render(suggest(reported(), settings), "feat_in_title", {}, settings)

    assert tags.title == f"{FACE_TITLE} (feat. zayok, edyn)"
    assert (tags.artist, tags.album_artist) == ("gunk", "gunk")


def test_the_reported_track_saves_with_its_face_in_the_file_name(tmp_path: Path) -> None:
    settings = Settings(library_dir=tmp_path, artist_folders=True)
    tags = render(suggest(reported(), settings), "artist_field", {}, settings)

    destination = plan_destination(tmp_path, tags, settings, "mp3")

    assert destination == tmp_path / "gunk" / f"gunk, zayok, edyn - {FACE_TITLE}.mp3"


@pytest.mark.parametrize("uploader", ["name01 \U0001f525", "Some Channel"])
def test_a_list_of_20_names_gives_one_main_artist_and_19_featured(uploader: str) -> None:
    suggestion = suggest(track("Paper Moon", uploader, [", ".join(TWENTY)]), Settings())

    assert suggestion.main_artists == TWENTY[:1]
    assert suggestion.featured == TWENTY[1:]


def test_the_uploader_leads_a_list_that_names_it_anywhere() -> None:
    suggestion = suggest(track("Paper Moon", "name07 \U0001f525", [", ".join(TWENTY)]), Settings())

    assert suggestion.main_artists == ["name07 \U0001f525"]
    assert suggestion.featured == [name for name in TWENTY if name != "name07 \U0001f525"]


def test_feat_in_title_lists_all_19_featured_names_and_the_tags_are_never_cut() -> None:
    settings = Settings()
    suggestion = suggest(track("Paper Moon", "name01 \U0001f525", [", ".join(TWENTY)]), settings)

    tags = render(suggestion, "feat_in_title", {}, settings)

    assert tags.title == f"Paper Moon (feat. {', '.join(TWENTY[1:])})"
    assert tags.artist == "name01 \U0001f525"


def test_a_file_name_with_19_features_is_cut_to_fit_the_windows_path_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(library, "MAX_PATH_UNITS", 259)
    settings = Settings(library_dir=tmp_path, artist_folders=True, title_style="feat_in_title")
    suggestion = suggest(track("Paper Moon", "name01 \U0001f525", [", ".join(TWENTY)]), settings)
    tags = render(suggestion, "feat_in_title", {}, settings)

    destination = plan_destination(tmp_path, tags, settings, "mp3")

    # Each 🔥 counts as two UTF-16 units, as Windows counts it.
    room = utf16_units(str(destination)) + library.TEMP_NAME_EXTRA
    assert room <= library.MAX_PATH_UNITS
    assert destination.name.startswith(
        "name01 \U0001f525 - Paper Moon (feat. name02 \U0001f525, name03 \U0001f525"
    )
    assert len(tags.title) > len(destination.stem)


def test_a_lone_duo_in_publisher_text_stays_one_artist() -> None:
    suggestion = suggest(track("Baddadan", "UKF", ["Chase & Status"]), Settings())

    assert (suggestion.main_artists, suggestion.featured) == (["Chase & Status"], [])


@pytest.mark.parametrize("symbol", ["\u2727", "\u2605", "\u2606", "\u2661", "\u266a"])
def test_symbols_that_arent_emoji_are_never_touched(symbol: str) -> None:
    source = track(f"{symbol} Paper Moon {symbol} FREE DL", f"kaia {symbol}", [])

    suggestion = suggest(source, Settings())

    assert suggestion.base_title == f"{symbol} Paper Moon {symbol}"
    assert suggestion.main_artists == [f"kaia {symbol}"]
    assert [change.kind for change in suggestion.changes] == ["remove_promo"]


def test_real_emoji_stay_with_their_removal_offered_switched_off() -> None:
    suggestion = suggest(
        track("\U0001f525 Paper \U0001f319 Moon \U0001f525", "kaia \U0001f319", []), Settings()
    )

    assert suggestion.base_title == "\U0001f525 Paper \U0001f319 Moon \U0001f525"
    assert suggestion.main_artists == ["kaia \U0001f319"]
    assert [(change.kind, change.enabled) for change in suggestion.changes] == [
        ("remove_emoji", False),
        ("remove_emoji", False),
        ("remove_emoji", False),
        ("remove_emoji", False),
    ]


def test_emoji_around_removed_promo_go_with_it() -> None:
    suggestion = suggest(track("Paper Moon \U0001f525 FREE DL \U0001f525", "Kaia", []), Settings())

    assert suggestion.base_title == "Paper Moon"
