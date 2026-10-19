"""Tests for the artist helpers in step 4: labels and channels."""

from scmp3.core.models import Settings, SourceInfo
from scmp3.core.suggest.artists import uploader_is_label
from scmp3.core.suggest.draft import Context
from scmp3.core.suggest.words import load_wordlists


def context_for(uploader: str, publisher: tuple[str, ...] = ()) -> Context:
    """A context for a source uploaded by `uploader`."""
    source = SourceInfo(kind="soundcloud", id="x", url="u", title="t", uploader=uploader)
    return Context(source, Settings(), load_wordlists(), publisher, uploader)


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


def test_an_uploader_that_is_the_publisher_artist_but_a_lost_letter_isnt_a_channel() -> None:
    assert not uploader_is_label(context_for("Michael Bubl\u00e9", ("Michael Bubl?",)))
    assert not uploader_is_label(context_for("Zoe", ("Zo\u00eb",)))


def test_an_uploader_named_like_a_website_is_a_channel() -> None:
    for uploader in ["Thissongissick.com", "deepdance.FM", "kaia.co", "Kaia.tv "]:
        assert uploader_is_label(context_for(uploader)), uploader


def test_a_dot_in_an_uploader_name_needs_a_whole_top_level_domain_at_the_end() -> None:
    for uploader in ["Lou.Cosmo", "kaia.community", ".com", "Kaia.com Fan"]:
        assert not uploader_is_label(context_for(uploader)), uploader
