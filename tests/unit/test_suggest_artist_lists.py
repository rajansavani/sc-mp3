"""Tests for step 9: choosing the main artists, merging the featured ones, and settling both."""

from scmp3.core.suggest.artist_lists import (
    TitleArtist,
    choose_main,
    credited_spelling,
    merge_featured,
    publisher_artists,
    settle,
)

BUBLE_LOST = "Michael Bubl?"
BUBLE_REPLACED = "Michael Bubl\ufffd"
BUBLE_ACCENT = "Michael Bubl\u00e9"
ZOE_ACCENT = "Zo\u00eb"


def plain(*names: str) -> list[TitleArtist]:
    """Title artists with nothing put back into them."""
    return [TitleArtist(name, name) for name in names]


def test_choose_main_without_publisher_artists_takes_the_title_artists_then_the_uploader() -> None:
    assert choose_main([], plain("Lou V"), "Some Channel") == ["Lou V"]
    assert choose_main([], [], "Some Channel") == ["Some Channel"]
    assert choose_main([], [], "") == []


def test_choose_main_keeps_the_publisher_list_and_adds_only_new_title_artists() -> None:
    assert choose_main(["Kaia"], plain("Kaia", "Lou V"), "x") == ["Kaia", "Lou V"]
    assert choose_main(["Chase & Status"], plain("Chase & Status"), "x") == ["Chase & Status"]
    # "Kaia & Lou V" names a publisher artist, so it doesn't join as a second credit.
    assert choose_main(["Kaia"], plain("Kaia & Lou V"), "x") == ["Kaia"]


def test_a_title_artist_with_a_feature_put_back_replaces_its_publisher_artist() -> None:
    restored = [TitleArtist("Kaia ft. Lou V", "Kaia")]

    assert choose_main(["Kaia", "Mara"], restored, "x") == ["Kaia ft. Lou V", "Mara"]


def test_choose_main_doesnt_add_a_title_credit_that_a_comma_list_names_in_full() -> None:
    publisher = ["Rival, Cadmium, Harley Bird"]

    assert choose_main(publisher, plain("Rival & Cadmium"), "x") == publisher
    assert choose_main(publisher, plain("Rival", "CADMIUM"), "x") == publisher
    assert choose_main(publisher, plain("Lou V"), "x") == [*publisher, "Lou V"]


def test_choose_main_adds_a_title_credit_that_a_comma_list_names_only_in_part() -> None:
    tyler = ["Tyler, The Creator"]

    assert choose_main(tyler, plain("Tyler, The Creator & Kaia"), "x") == [
        *tyler,
        "Tyler, The Creator & Kaia",
    ]
    assert choose_main(["Kaia, Mara"], plain("Kaia & Lou V"), "x") == ["Kaia, Mara", "Kaia & Lou V"]


def test_choose_main_adds_a_title_artist_with_a_feature_put_back_even_in_a_comma_list() -> None:
    restored = [TitleArtist("Rival ft. Harley Bird", "Rival", ("Harley Bird",))]
    publisher = ["Rival, Cadmium, Harley Bird"]

    assert choose_main(publisher, restored, "x") == [*publisher, "Rival ft. Harley Bird"]


def test_choose_main_takes_the_title_spelling_of_a_publisher_artist_with_a_lost_letter() -> None:
    assert choose_main([BUBLE_LOST], plain("Michael Buble"), "x") == ["Michael Buble"]
    assert choose_main([BUBLE_LOST], plain(BUBLE_ACCENT), "x") == [BUBLE_ACCENT]
    assert choose_main([BUBLE_REPLACED], plain("Michael Buble"), "x") == ["Michael Buble"]
    assert choose_main([BUBLE_ACCENT], plain(BUBLE_LOST), "x") == [BUBLE_ACCENT]
    assert choose_main([ZOE_ACCENT], plain("Zoe"), "x") == [ZOE_ACCENT]


def test_choose_main_takes_the_uploader_spelling_of_an_artist_with_a_lost_letter() -> None:
    assert choose_main([BUBLE_LOST], [], BUBLE_ACCENT) == [BUBLE_ACCENT]
    # The uploader's spelling comes first, so it wins over the title's.
    assert choose_main([BUBLE_LOST], plain("Michael Buble"), BUBLE_ACCENT) == [BUBLE_ACCENT]
    # A title artist's lost letter is filled in too.
    assert choose_main(["Kaia"], plain(BUBLE_LOST), BUBLE_ACCENT) == ["Kaia", BUBLE_ACCENT]
    assert choose_main([], plain(BUBLE_LOST), BUBLE_ACCENT) == [BUBLE_ACCENT]


def test_choose_main_leaves_a_lost_letter_when_the_uploader_is_someone_else() -> None:
    assert choose_main([BUBLE_LOST], [], "Warner Records") == [BUBLE_LOST]
    assert choose_main([BUBLE_LOST], [], "Michael Bubl?") == [BUBLE_LOST]


def test_merge_featured_lists_each_artist_once_spelled_as_the_artist_field_has_it() -> None:
    merged = merge_featured(["Zoe", "Lou V"], [ZOE_ACCENT, "Mara"], "Kaia")

    assert merged == [ZOE_ACCENT, "Lou V", "Mara"]
    assert merge_featured(["Michael Buble"], [BUBLE_LOST], "Kaia") == ["Michael Buble"]
    assert merge_featured(["Lou V"], ["lou v"], "Kaia") == ["lou v"]


def test_merge_featured_fills_a_lost_letter_from_the_uploader() -> None:
    assert merge_featured([], [BUBLE_LOST], BUBLE_ACCENT) == [BUBLE_ACCENT]
    assert merge_featured([BUBLE_LOST], [], BUBLE_ACCENT) == [BUBLE_ACCENT]
    assert merge_featured([], [BUBLE_LOST], "Kaia") == [BUBLE_LOST]


def test_choose_main_keeps_the_accented_spelling_when_no_publisher_spells_the_name() -> None:
    assert choose_main([], plain("Zoe", ZOE_ACCENT), "x") == [ZOE_ACCENT]
    assert choose_main(["Kaia"], plain("Zoe", ZOE_ACCENT), "x") == ["Kaia", ZOE_ACCENT]
    assert choose_main([], plain("KAIA", "Kaia"), "x") == ["KAIA"]


def test_credited_spelling_is_the_publisher_spelling_with_a_lost_letter_filled_in() -> None:
    publisher = ["Kaia", "Lou V", ZOE_ACCENT, BUBLE_LOST, "Rival, Cadmium, Harley Bird"]

    assert credited_spelling("LOU V", publisher, "x") == "Lou V"
    assert credited_spelling("Zoe", publisher, "x") == ZOE_ACCENT
    assert credited_spelling("Michael Buble", publisher, "x") == "Michael Buble"
    assert credited_spelling("HARLEY BIRD", publisher, "x") == "Harley Bird"
    assert credited_spelling(BUBLE_LOST, [], BUBLE_ACCENT) == BUBLE_ACCENT
    assert credited_spelling("Mara", publisher, "x") == "Mara"


def test_settle_moves_a_featured_name_out_of_the_main_list() -> None:
    assert settle(["Kaia", "Lou V"], ["Lou V"]) == (["Kaia"], ["Lou V"])


def test_settle_keeps_a_main_artist_who_is_also_featured_as_main_only() -> None:
    assert settle(["Kaia"], ["kaia", "Lou V"]) == (["Kaia"], ["Lou V"])
    assert settle(["Kaia"], ["kaia"]) == (["Kaia"], [])


def test_settle_counts_near_spellings_as_one_artist_and_keeps_the_main_spelling() -> None:
    assert settle(["Kaia", BUBLE_LOST], ["Michael Buble"]) == (["Kaia"], ["Michael Buble"])
    assert settle(["Kaia", ZOE_ACCENT], ["Zoe"]) == (["Kaia"], [ZOE_ACCENT])
    assert settle([ZOE_ACCENT], ["Zoe"]) == ([ZOE_ACCENT], [])
    assert settle([BUBLE_LOST], ["Michael Buble"]) == (["Michael Buble"], [])


def test_settle_drops_a_featured_name_that_a_comma_joined_main_artist_already_names() -> None:
    main = ["Rival, Cadmium, Harley Bird"]

    assert settle(main, ["harley bird", "Lou V"]) == (main, ["Lou V"])
    assert settle(["Okoye", "Tyler, The Creator"], ["Tyler, The Creator"]) == (
        ["Okoye"],
        ["Tyler, The Creator"],
    )


def test_publisher_artists_make_the_listed_uploader_main_and_the_others_featured() -> None:
    publisher = publisher_artists([["gunk", "zayok", "edyn"]], [], ["gunk"])

    assert (publisher.main, publisher.featured) == (["gunk"], ["zayok", "edyn"])
    assert publisher.listed == ["gunk", "zayok", "edyn"]


def test_publisher_artists_make_the_first_name_main_when_the_uploader_isnt_listed() -> None:
    publisher = publisher_artists([["Rival", "Cadmium", "Harley Bird"]], [], ["NCS"])

    assert (publisher.main, publisher.featured) == (["Rival"], ["Cadmium", "Harley Bird"])


def test_publisher_artists_find_the_uploader_by_any_of_its_spellings() -> None:
    publisher = publisher_artists([["zayok", "GUNK \U0001f525"]], [], ["gunk", "GUNK \U0001f525"])

    assert (publisher.main, publisher.featured) == (["GUNK \U0001f525"], ["zayok"])


def test_publisher_artists_keep_names_given_one_by_one_all_main() -> None:
    publisher = publisher_artists([["Kaia"], ["Lou V"]], [], ["Lou V"])

    assert (publisher.main, publisher.featured, publisher.listed) == (["Kaia", "Lou V"], [], [])


def test_choose_main_adds_a_title_duo_only_when_a_list_doesnt_name_both() -> None:
    listed = ["Kaia", "Mara"]

    assert choose_main(["Kaia"], plain("Kaia & Mara"), "x", listed) == ["Kaia"]
    assert choose_main(["Kaia"], plain("Kaia & Lou V"), "x", listed) == ["Kaia", "Kaia & Lou V"]


def test_publisher_artists_keep_every_listed_name_a_title_duo_credits_main() -> None:
    names = [["Rival", "Cadmium", "Harley Bird"]]

    publisher = publisher_artists(names, plain("cadmium & Rival"), ["NCS"])

    assert (publisher.main, publisher.featured) == (["Cadmium", "Rival"], ["Harley Bird"])


def test_publisher_artists_use_the_uploader_rule_when_the_title_credits_one_listed_name() -> None:
    names = [["gunk", "zayok", "edyn"]]

    publisher = publisher_artists(names, plain("zayok & Lou V"), ["gunk"])

    assert (publisher.main, publisher.featured) == (["gunk"], ["zayok", "edyn"])


def test_publisher_artists_make_every_listed_name_of_a_duo_uploader_main() -> None:
    names = [["Harley Bird", "Rival", "Cadmium"]]

    publisher = publisher_artists(names, [], ["Rival & Cadmium"])

    assert (publisher.main, publisher.featured) == (["Rival", "Cadmium"], ["Harley Bird"])
