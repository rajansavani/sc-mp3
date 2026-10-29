"""Listed artists the title, the description or their own name credits as producers, as zayok is
in "Song prod zayok" from gunk listing "gunk, zayok", or "Prod. H2T" is by name. They aren't main or
featured while another artist is credited, and their credit is offered switched off."""

import re
from collections.abc import Mapping, Sequence

from scmp3.core.models import Change
from scmp3.core.suggest.draft import Draft, names_pattern
from scmp3.core.suggest.labels import change_label, new_change_id
from scmp3.core.suggest.names import any_same_artist, split_names
from scmp3.core.suggest.words import Wordlists, pattern

KIND = "credit_producer"
# A tracklist line, like "1. edyn - other song (prod. zayok)", credits another track.
_TRACKLIST_LINE = re.compile(r"^\s*\d{1,3}(?:\s*[.):-]|\s)")


def is_producer_credit(name: str, words: Wordlists) -> bool:
    """Whether a name is itself a producer credit, like "Prod. H2T" or "prod. by Hazy"."""
    return bool(pattern(rf"^\W*{words.producer}\s*:?\s*\w").search(name))


def marked_producers(
    names: Sequence[str], title: Draft, description: str | None, words: Wordlists
) -> list[str]:
    """The `names` that are producer credits, or that one in the title or description names."""
    credits = [title.span(claim) for claim in title.claims if claim.kind == "remove_producer"]
    lines = (description or "").splitlines()
    credits += [line for line in lines if not _TRACKLIST_LINE.search(line)]
    producers = [name for credit in credits for name in _producer_names(credit, words)]
    return [
        name
        for name in names
        if is_producer_credit(name, words) or any_same_artist([name], producers)
    ]


def _producer_names(text: str, words: Wordlists) -> list[str]:
    """The names after each producer keyword in `text`, as in "(prod. Hazy & @Lou V)"."""
    credit = pattern(rf"{words.producer}\s*:?\s*{names_pattern(words.feature)}")
    return [
        name.lstrip("@") for match in credit.finditer(text) for name in split_names(match["names"])
    ]


def producer_changes(
    producers: Sequence[str], toggles: Mapping[str, bool], taken: dict[str, int]
) -> list[Change]:
    """One change per listed producer, which credits them as featured. It starts off."""
    changes: list[Change] = []
    for name in producers:
        change_id = new_change_id(KIND, name, taken)
        label = change_label(KIND, name)
        changes.append(Change(change_id, KIND, name, label, toggles.get(change_id, False)))
    return changes


def held_back(changes: Sequence[Change]) -> list[str]:
    """The producers whose credit is switched off, so they aren't featured."""
    return [change.span for change in changes if change.kind == KIND and not change.enabled]
