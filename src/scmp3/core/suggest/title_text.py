"""The title and version a draft gives with its changes applied, and the offer of title case."""

from collections.abc import Mapping

from scmp3.core.models import Change
from scmp3.core.suggest import artist_lists, rules
from scmp3.core.suggest.assembly import assemble
from scmp3.core.suggest.draft import Claim, Draft
from scmp3.core.suggest.labels import change_label, new_change_id
from scmp3.core.suggest.words import Wordlists


def caps_span(draft: Draft) -> str | None:
    """The title as the default changes leave it, when it's all in capitals.

    A title that's nothing but promo or credits, like "[FREE DL]" alone, isn't offered.
    """
    own_words = rules.tidy(" ".join(assemble(draft, cut=lambda claim: True)))
    kept = rules.tidy(" ".join(assemble(draft, cut=lambda claim: claim.enabled)))
    return kept if own_words and rules.is_all_caps(kept) else None


def caps_change(
    span: str | None, toggles: Mapping[str, bool], taken: dict[str, int]
) -> Change | None:
    """Offer title case for an all-caps title. It starts off, and never touches artist names."""
    if span is None:
        return None
    change_id = new_change_id("fix_caps", span, taken)
    label = change_label("fix_caps", span)
    return Change(change_id, "fix_caps", span, label, toggles.get(change_id, False))


def title_and_version(draft: Draft, words: Wordlists, *, caps_on: bool) -> tuple[str, str | None]:
    """The base title and version, with the changes that are on applied."""
    carried = artist_lists.carried_features(draft)
    moved = artist_lists.carried_versions(draft)

    def cut(claim: Claim) -> bool:
        # A feature inside a taken artist part leaves the title with it, on or off.
        return claim.enabled or claim.start in carried

    def transform(text: str) -> str:
        return rules.title_case(text, words.caps_keep) if caps_on else text

    rebuilt = assemble(draft, cut=cut, transform=transform, moved=moved)
    title, version = rules.carry_open_bracket(*rebuilt)
    # The version keeps the space before it, or the lack of one, as written.
    spaced = not title or title[-1].isspace() or version[:1].isspace()
    base_title = rules.tidy(title)
    kept_version = rules.tidy_version(version, spaced=spaced)
    # A title that is only a version, like "(Intro)", keeps it as the title.
    if not base_title and kept_version:
        return kept_version.strip(), None
    return base_title, kept_version
