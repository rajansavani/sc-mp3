"""Tests for the artist helpers: labels and channels, choosing the main artists, and settling."""

from scmp3.core.models import Settings, SourceInfo
from scmp3.core.suggest.artists import TitleArtist, choose_main, settle, uploader_is_label
from scmp3.core.suggest.draft import Context
from scmp3.core.suggest.words import load_wordlists


def context_for(uploader: str, publisher: tuple[str, ...] = ()) -> Context:
    """A context for a source uploaded by `uploader`."""
    source = SourceInfo(kind="soundcloud", id="x", url="u", title="t", uploader=uploader)
    return Context(source, Settings(), load_wordlists(), publisher, uploader)


def plain(*names: str) -> list[TitleArtist]:
    """Title artists with nothing put back into them."""
    return [TitleArtist(name, name) for name in names]


def test_an_uploader_with_a_label_word_is_a_label() -> None:
    assert uploader_is_label(context_for("Monstercat Records"))
    assert uploader_is_label(context_for("Kaia Music"))


def test_label_words_match_whole_words_only() -> None:
    assert not uploader_is_label(context_for("Musicology"))


def test_a_known_channel_is_a_label_ignoring_case_spaces_and_punctuation() -> None:
    assert uploader_is_label(context_for("Lyrical Lemonade"))
    assert uploader_is_label(context_for("MR SUICIDE SHEEP"))
    assert uploader_is_label(context_for("trap-nation"))
    assert not uploader_is_label(context_for("Lyrical Lemonade Fan"))


def test_an_uploader_that_isnt_a_publisher_artist_is_a_channel() -> None:
    assert uploader_is_label(context_for("Lyrical Lemonade", ("Juice WRLD",)))
    assert not uploader_is_label(context_for("juicewrld", ("Juice WRLD",)))


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


def test_settle_moves_a_featured_name_out_of_the_main_list() -> None:
    assert settle(["Kaia", "Lou V"], ["Lou V"]) == (["Kaia"], ["Lou V"])


def test_settle_keeps_a_main_artist_who_is_also_featured_as_main_only() -> None:
    assert settle(["Kaia"], ["kaia", "Lou V"]) == (["Kaia"], ["Lou V"])
    assert settle(["Kaia"], ["kaia"]) == (["Kaia"], [])
