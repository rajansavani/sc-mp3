"""Tests for rendering a suggestion: presets, custom templates, settings and overrides."""

import pytest

from scmp3.core.models import (
    ArtistSeparator,
    FeatWording,
    Settings,
    SinglesAlbum,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.suggest import render
from scmp3.core.suggest.render import InvalidOverride, credits_name, fill

FULL = TagSuggestion(
    base_title="Midnight Drive",
    main_artists=["Kaia"],
    featured=["Lou V", "Mara"],
    version=" (VIP Mix)",
    year=2026,
    genre="Hip-hop",
)
PLAIN = TagSuggestion(base_title="Midnight Drive", main_artists=["Kaia"])
VALUES = {
    "title": "Midnight Drive",
    "version": "",
    "main": "Kaia",
    "featured": "",
    "artists": "Kaia",
}


@pytest.mark.parametrize(
    ("style", "title", "artist"),
    [
        ("artist_field", "Midnight Drive (VIP Mix)", "Kaia, Lou V, Mara"),
        ("feat_in_title", "Midnight Drive (feat. Lou V, Mara) (VIP Mix)", "Kaia"),
        ("all_in_title", "Midnight Drive (Kaia, Lou V, Mara) (VIP Mix)", "Kaia"),
    ],
)
def test_each_preset_renders_as_the_spec_table_says(
    style: TitleStyle, title: str, artist: str
) -> None:
    tags = render(FULL, style, {}, Settings())

    assert (tags.title, tags.artist) == (title, artist)


@pytest.mark.parametrize("style", ["artist_field", "feat_in_title", "all_in_title"])
def test_presets_leave_no_stray_brackets_or_feat_without_features(style: TitleStyle) -> None:
    tags = render(PLAIN, style, {}, Settings())

    assert "()" not in tags.title
    assert "feat" not in tags.title
    assert tags.artist == "Kaia"


def test_no_style_means_the_style_setting() -> None:
    settings = Settings(title_style="feat_in_title")

    assert render(FULL, None, {}, settings).title == "Midnight Drive (feat. Lou V, Mara) (VIP Mix)"


def test_the_artist_separator_setting_joins_every_artist_list() -> None:
    settings = Settings(artist_separator=" & ")

    assert render(FULL, "artist_field", {}, settings).artist == "Kaia & Lou V & Mara"
    assert render(FULL, "feat_in_title", {}, settings).title == (
        "Midnight Drive (feat. Lou V & Mara) (VIP Mix)"
    )


@pytest.mark.parametrize("wording", ["ft.", "featuring", "with"])
def test_the_feat_wording_setting_is_used_in_feat_in_title(wording: FeatWording) -> None:
    settings = Settings(feat_wording=wording)

    tags = render(FULL, "feat_in_title", {}, settings)

    assert tags.title == f"Midnight Drive ({wording} Lou V, Mara) (VIP Mix)"


def test_custom_templates_come_from_the_settings() -> None:
    settings = Settings(
        custom_title_template="{title} [w/ {featured}]{version}",
        custom_artist_template="{main} x {featured}",
    )

    tags = render(FULL, "custom", {}, settings)

    assert (tags.title, tags.artist) == (
        "Midnight Drive [w/ Lou V, Mara] (VIP Mix)",
        "Kaia x Lou V, Mara",
    )


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{title} (feat. {featured}){version}", "Midnight Drive"),
        ("{title} [feat. {featured}]", "Midnight Drive"),
        ("{title} feat. {featured}", "Midnight Drive"),
        ("{title} - {version}", "Midnight Drive"),
        ("{title} ({main} feat. {featured})", "Midnight Drive (Kaia)"),
        ("{title} ({main}) feat. {featured}", "Midnight Drive (Kaia)"),
        ("{title} ([{featured}])", "Midnight Drive"),
        ("feat. {featured}", ""),
        ("{featured} x {main}", "Kaia"),
        ("{featured} & {main}", "Kaia"),
        ("{title} ({featured} x {main})", "Midnight Drive (Kaia)"),
        ("{main} - {version}", "Kaia"),
    ],
)
def test_custom_templates_drop_empty_groups(template: str, expected: str) -> None:
    assert fill(template, VALUES) == expected


def test_unknown_tokens_are_left_as_written() -> None:
    assert fill("{title} {album}", VALUES) == "Midnight Drive {album}"


def test_an_empty_title_renders_empty_in_every_style() -> None:
    empty = TagSuggestion(base_title="", main_artists=["Kaia"])
    styles: list[TitleStyle] = ["artist_field", "feat_in_title", "all_in_title"]

    for style in styles:
        assert render(empty, style, {}, Settings()).title == ""


def test_hand_typed_overrides_win_over_every_rendered_field() -> None:
    overrides = {
        "title": "My Title",
        "artist": "My Artist",
        "album": "My Album",
        "album_artist": "Someone",
        "year": "1999",
        "genre": "House",
        "track_no": "3",
        "track_total": "12",
    }

    tags = render(FULL, "artist_field", overrides, Settings())

    assert (tags.title, tags.artist, tags.album, tags.album_artist) == (
        "My Title",
        "My Artist",
        "My Album",
        "Someone",
    )
    assert (tags.year, tags.genre, tags.track_no, tags.track_total) == (1999, "House", 3, 12)


def test_blank_overrides_clear_optional_fields() -> None:
    tags = render(FULL, "artist_field", {"year": " ", "genre": ""}, Settings())

    assert (tags.year, tags.genre) == (None, None)


@pytest.mark.parametrize(
    ("field", "typed"),
    [("year", "20x6"), ("track_no", "3 of 12"), ("track_no", "3/"), ("track_total", "\u0663")],
)
def test_a_number_override_that_isnt_a_number_is_refused_with_the_field(
    field: str, typed: str
) -> None:
    with pytest.raises(InvalidOverride) as raised:
        render(FULL, "artist_field", {field: typed}, Settings())

    assert (raised.value.field, raised.value.value) == (field, typed)


@pytest.mark.parametrize("typed", ["3/12", "3 / 12", " 3/ 12 "])
def test_a_typed_track_number_with_a_total_fills_both(typed: str) -> None:
    tags = render(FULL, "artist_field", {"track_no": typed}, Settings())

    assert (tags.track_no, tags.track_total) == (3, 12)


def test_the_package_exports_the_override_error_with_render() -> None:
    import scmp3.core.suggest as package

    assert package.InvalidOverride is InvalidOverride
    assert "InvalidOverride" in package.__all__


def test_a_typed_track_total_wins_over_one_typed_with_the_number() -> None:
    tags = render(FULL, "artist_field", {"track_no": "3/12", "track_total": "10"}, Settings())

    assert (tags.track_no, tags.track_total) == (3, 10)


def test_a_typed_track_number_alone_keeps_the_suggested_total() -> None:
    in_a_set = TagSuggestion(base_title="Song", main_artists=["Kaia"], track_no=2, track_total=9)

    tags = render(in_a_set, "artist_field", {"track_no": "4"}, Settings())

    assert (tags.track_no, tags.track_total) == (4, 9)


def test_overrides_for_fields_that_arent_tags_are_ignored() -> None:
    tags = render(FULL, "artist_field", {"filename": "x"}, Settings())

    assert tags.title == "Midnight Drive (VIP Mix)"


@pytest.mark.parametrize("style", ["artist_field", "feat_in_title", "all_in_title"])
def test_the_album_for_singles_is_the_title_without_artists_in_every_style(
    style: TitleStyle,
) -> None:
    assert render(FULL, style, {}, Settings()).album == "Midnight Drive (VIP Mix)"


def test_the_album_for_singles_follows_a_hand_typed_title() -> None:
    assert render(FULL, "artist_field", {"title": "Typed"}, Settings()).album == "Typed"


@pytest.mark.parametrize(
    ("setting", "text", "album"),
    [("fixed", "Singles", "Singles"), ("blank", "Singles", "")],
)
def test_the_album_for_singles_setting_picks_fixed_text_or_blank(
    setting: SinglesAlbum, text: str, album: str
) -> None:
    settings = Settings(singles_album=setting, singles_album_text=text)

    assert render(FULL, "artist_field", {}, settings).album == album


def test_an_album_on_the_suggestion_wins_over_the_singles_setting() -> None:
    with_album = TagSuggestion(base_title="Song", main_artists=["Kaia"], album="Lantern")

    assert (
        render(with_album, "artist_field", {}, Settings(singles_album="blank")).album == "Lantern"
    )


def test_the_album_artist_is_the_first_main_artist_unless_the_suggestion_has_one() -> None:
    duo = TagSuggestion(base_title="Song", main_artists=["Kaia", "Lou V"])
    with_album_artist = TagSuggestion(base_title="Song", main_artists=["Kaia"], album_artist="Mara")

    assert render(duo, "artist_field", {}, Settings()).album_artist == "Kaia"
    assert render(with_album_artist, "artist_field", {}, Settings()).album_artist == "Mara"


@pytest.mark.parametrize(
    "typed",
    [
        "Kaia, Lou Vee, Mara",
        "Kaia, Lou V, Mara, Hazy",
        "kaia",
        "Kaïa, Lou V",
        "Lou V, Kaia",
        "Kaia,",
    ],
)
def test_a_typed_artist_that_still_names_the_album_artist_keeps_it(typed: str) -> None:
    tags = render(FULL, "artist_field", {"artist": typed}, Settings())

    assert (tags.artist, tags.album_artist) == (typed, "Kaia")


@pytest.mark.parametrize(
    "typed",
    ["Mara Lune", "Kaiak, Lou V", "Kaia & Lou V", "Kaia Lou V", "Tyler, The Creator"],
)
def test_a_typed_artist_that_no_longer_names_the_album_artist_replaces_it_whole(
    typed: str,
) -> None:
    tags = render(FULL, "artist_field", {"artist": typed}, Settings())

    assert tags.album_artist == typed


@pytest.mark.parametrize("typed", ["Kaia & Lou Vee & Mara", "Lou V & Kaia", "Kaia & Lou V, Hazy"])
def test_under_the_ampersand_separator_editing_a_feature_keeps_the_album_artist(
    typed: str,
) -> None:
    tags = render(FULL, "artist_field", {"artist": typed}, Settings(artist_separator=" & "))

    assert tags.album_artist == "Kaia"


@pytest.mark.parametrize("typed", ["Kaia x Lou Vee x Mara", "Kaia X Lou Vee"])
def test_under_the_x_separator_editing_a_feature_keeps_the_album_artist(typed: str) -> None:
    tags = render(FULL, "artist_field", {"artist": typed}, Settings(artist_separator=" x "))

    assert tags.album_artist == "Kaia"


@pytest.mark.parametrize(
    ("separator", "typed"),
    [(" & ", "Mara Lune & Lou V"), (" x ", "Kaiax Lou V"), (", ", "Kaia & Lou Vee")],
)
def test_the_separator_is_only_for_matching_so_a_new_artist_stays_whole(
    separator: ArtistSeparator, typed: str
) -> None:
    tags = render(FULL, "artist_field", {"artist": typed}, Settings(artist_separator=separator))

    assert tags.album_artist == typed


def test_a_typed_artist_keeps_an_album_artist_with_a_separator_of_its_own() -> None:
    duo = TagSuggestion(base_title="Song", main_artists=["Chase & Status"])
    settings = Settings(artist_separator=" & ")

    kept = render(duo, "artist_field", {"artist": "Chase & Status & Kaia"}, settings)
    replaced = render(duo, "artist_field", {"artist": "Chase & Kaia"}, settings)

    assert (kept.album_artist, replaced.album_artist) == ("Chase & Status", "Chase & Kaia")


def test_a_typed_artist_keeps_an_album_artist_with_a_comma_of_its_own() -> None:
    tyler = TagSuggestion(base_title="Song", main_artists=["Tyler, The Creator"])

    tags = render(tyler, "artist_field", {"artist": "Tyler, The Creator, Kaia"}, Settings())

    assert tags.album_artist == "Tyler, The Creator"


def test_a_typed_artist_is_checked_against_the_suggestions_album_artist() -> None:
    with_album_artist = TagSuggestion(base_title="Song", main_artists=["Kaia"], album_artist="Mara")

    kept = render(with_album_artist, "artist_field", {"artist": "Mara, Kaia"}, Settings())
    replaced = render(with_album_artist, "artist_field", {"artist": "Kaia"}, Settings())

    assert (kept.album_artist, replaced.album_artist) == ("Mara", "Kaia")


@pytest.mark.parametrize("typed_album_artist", ["Someone", ""])
def test_a_typed_album_artist_always_wins_over_a_typed_artist(typed_album_artist: str) -> None:
    overrides = {"artist": "Mara Lune", "album_artist": typed_album_artist}

    assert render(FULL, "artist_field", overrides, Settings()).album_artist == typed_album_artist


def test_year_and_genre_come_from_the_suggestion() -> None:
    tags = render(FULL, "artist_field", {}, Settings())

    assert (tags.year, tags.genre, tags.track_no) == (2026, "Hip-hop", None)


def test_feat_in_title_leaves_out_names_the_title_already_shows() -> None:
    suggestion = TagSuggestion(
        base_title="Sweeeep w/ sydneyrunner @zjay",
        main_artists=["edyn"],
        featured=["sydney runner", "zjay", "Mara"],
    )

    tags = render(suggestion, "feat_in_title", {}, Settings())
    in_artist_field = render(suggestion, "artist_field", {}, Settings())

    assert tags.title == "Sweeeep w/ sydneyrunner @zjay (feat. Mara)"
    assert in_artist_field.artist == "edyn, sydney runner, zjay, Mara"


@pytest.mark.parametrize(
    ("text", "name", "credited"),
    [
        ("boombox [edyn]", "edyn", True),
        ("face off w raison (edyn gabby)", "edyn", True),
        ("hold that frown w/ EDYN (sol)", "edyn", True),
        ("face off w raison", "raison", True),
        ("yanu ; edyn", "edyn", True),
        ("ex-freundin +Nosgov", "Nosgov", True),
        ("Sweeeep w/ sydneyrunner @zjay", "zjay", True),
        ("Sweeeep w/ sydneyrunner", "sydney runner", True),
        ("Rival & Cadmium - Seasons", "Cadmium", True),
        ("Back to the Future", "Future", False),
        ("eri tash", "tash", False),
        ("w/ edynx", "edyn", False),
        ("Song", "", False),
    ],
)
def test_credits_name_needs_a_credit_marker_or_brackets(
    text: str, name: str, credited: bool
) -> None:
    assert credits_name(text, name) is credited


def test_feat_in_title_keeps_a_name_the_title_only_mentions() -> None:
    suggestion = TagSuggestion(
        base_title="Back to the Future", main_artists=["Kaia"], featured=["Future"]
    )

    tags = render(suggestion, "feat_in_title", {}, Settings())

    assert tags.title == "Back to the Future (feat. Future)"
