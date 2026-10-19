"""Builds a TagSuggestion: runs the rules over the title in order, then settles each change."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from scmp3.core.models import Change, Settings, SourceInfo, TagSuggestion
from scmp3.core.suggest import artists, rules
from scmp3.core.suggest.draft import VERSION, Claim, Context, Draft, assemble
from scmp3.core.suggest.labels import change_label
from scmp3.core.suggest.names import name_key
from scmp3.core.suggest.words import Wordlists, load_wordlists

Step = Callable[[Draft, Context], None]

# The title rules in the order they claim text. An earlier claim wins where two rules overlap.
TITLE_STEPS: tuple[Step, ...] = (
    rules.claim_bracket_versions,  # first, since keep-list words are never removed
    rules.claim_bracket_promo,  # before features, so "(With Lyrics)" isn't a feature
    artists.claim_bracket_features,
    rules.claim_bracket_producers,
    rules.claim_leading_promo,
    artists.claim_pipe_artist,
    rules.claim_pipe_segment,
    rules.claim_trailing_promo,
    rules.claim_emoji,
    rules.claim_inline_producers,
    artists.claim_title_artist,
    artists.claim_inline_features,
    rules.claim_custom_words,
    # Again, for promo that only ends the title once a feature is claimed: "Song FREE DL ft. X".
    rules.claim_trailing_promo,
    rules.claim_remaining_emoji,  # last, once everything around each emoji is claimed
)
# The rules for a publisher artist name such as "Kaia feat. Lou V".
ARTIST_FIELD_STEPS: tuple[Step, ...] = (
    artists.claim_bracket_features,
    artists.claim_inline_features,
)
# The rules for the uploader's name, which becomes the artist when nothing else names one.
UPLOADER_STEPS: tuple[Step, ...] = (rules.claim_emoji,)
# Settings that decide whether a kind of change starts on.
SETTING_FOR_KIND = {
    "remove_promo": "remove_promo_text",
    "remove_producer": "remove_producer_credits",
}


def suggest(source: SourceInfo, settings: Settings) -> TagSuggestion:
    """Suggest tags for a source, with every change at its default."""
    return build(source, settings, toggles={})


def apply_changes(
    suggestion: TagSuggestion, source: SourceInfo, settings: Settings
) -> TagSuggestion:
    """Rebuild the title and artists with each change on or off as `suggestion` says.

    The other fields are kept, so album details set for a set survive a toggle.
    """
    toggles = {change.id: change.enabled for change in suggestion.changes}
    rebuilt = build(source, settings, toggles)
    return replace(
        suggestion,
        base_title=rebuilt.base_title,
        main_artists=rebuilt.main_artists,
        featured=rebuilt.featured,
        version=rebuilt.version,
        changes=rebuilt.changes,
    )


def build(source: SourceInfo, settings: Settings, toggles: Mapping[str, bool]) -> TagSuggestion:
    """Run every rule, then settle each change from `toggles` or its default."""
    words = load_wordlists()
    drafts = _detect(source, settings, words)
    caps_span = _caps_span(drafts.title)
    changes = _changes(drafts, toggles)
    caps = _caps_change(caps_span, toggles, drafts.taken)
    if caps:
        changes.append(caps)
    caps_on = bool(caps and caps.enabled)
    base_title, version = _title_and_version(drafts.title, words, caps_on=caps_on)
    main, featured = _artists(drafts)
    when = source.release_date or source.upload_date
    return TagSuggestion(
        base_title=base_title,
        main_artists=main,
        featured=featured,
        version=version,
        album=source.album or None,
        year=when.year if when else None,
        genre=(source.genre or "").strip().lstrip("#").strip() or None,
        changes=changes,
    )


@dataclass
class _Drafts:
    """The drafts of one source's title, publisher artists and uploader name."""

    title: Draft
    credits: list[Draft]
    uploader: Draft
    # The uploader's emoji change only shows when the uploader is the artist to begin with.
    uploader_is_artist: bool
    taken: dict[str, int] = field(default_factory=dict)  # change ids used so far


def _detect(source: SourceInfo, settings: Settings, words: Wordlists) -> _Drafts:
    """Run the rules on every text, with each claim at its default."""
    context = Context(source, settings, words)
    uploader = _run(source.uploader, UPLOADER_STEPS, context)
    credits = [_run(name, ARTIST_FIELD_STEPS, context) for name in source.artists]
    credits = [credit for credit in credits if credit.text]
    context = replace(
        context,
        publisher=tuple(_name(credit, cut_all=True) for credit in credits),
        uploader=_name(uploader, cut_all=True),
    )
    title = _run(source.title, TITLE_STEPS, context)
    _apply_settings(title, settings)
    _keep_title_from_emptying(title)
    uploader_is_artist = not credits and not artists.title_artists(title)
    return _Drafts(title, credits, uploader, uploader_is_artist)


def _changes(drafts: _Drafts, toggles: Mapping[str, bool]) -> list[Change]:
    """Settle the title's changes, then the publisher artists' and the uploader's."""
    changes = _settle(drafts.title, toggles, drafts.taken)
    for credit in drafts.credits:
        changes.extend(_settle(credit, toggles, drafts.taken, follow=drafts.title))
    if drafts.uploader_is_artist:
        changes.extend(_settle(drafts.uploader, toggles, drafts.taken))
    return changes


def _artists(drafts: _Drafts) -> tuple[list[str], list[str]]:
    """Step 9: the main and featured artists, given which changes are on."""
    featured = _names(drafts.title, "extract_feature") + [
        name for credit in drafts.credits for name in _names(credit, "extract_feature")
    ]
    main = artists.choose_main(
        [_name(credit, cut_all=False) for credit in drafts.credits],
        artists.title_artists(drafts.title),
        _name(drafts.uploader, cut_all=not drafts.uploader_is_artist),
    )
    return artists.settle(main, featured)


def _run(text: str, steps: tuple[Step, ...], context: Context) -> Draft:
    """A draft of `text` with every step's claims on it."""
    draft = Draft(rules.normalize(text))
    for step in steps:
        step(draft, context)
    return draft


def _apply_settings(draft: Draft, settings: Settings) -> None:
    """Start promo and producer changes off when the settings say to keep that text."""
    draft.claims = [
        replace(claim, enabled=False)
        if claim.kind in SETTING_FOR_KIND and not getattr(settings, SETTING_FOR_KIND[claim.kind])
        else claim
        for claim in draft.claims
    ]


def _keep_title_from_emptying(draft: Draft) -> None:
    """Start every removal off when together they'd leave no title, as with "[FREE DL]" alone."""
    title, version = assemble(draft, cut=lambda claim: claim.enabled)
    if rules.tidy(title) or rules.tidy_version(version) or not draft.text:
        return
    draft.claims = [
        claim if claim.kind == VERSION else replace(claim, enabled=False) for claim in draft.claims
    ]


def _settle(
    draft: Draft, toggles: Mapping[str, bool], taken: dict[str, int], follow: Draft | None = None
) -> list[Change]:
    """Turn a draft's claims into changes, in text order, switched as `toggles` says.

    With `follow`, a feature that the followed draft already credits shares its change.
    """
    settled: list[Claim] = []
    changes: list[Change] = []
    for claim in sorted(draft.claims, key=lambda claim: claim.start):
        leader = _same_feature(claim, follow) if follow else None
        if claim.kind == VERSION or leader:
            settled.append(replace(claim, enabled=leader.enabled) if leader else claim)
            continue
        span = draft.span(claim)
        change_id = _new_id(claim.kind, span, taken)
        enabled = toggles.get(change_id, claim.enabled)
        settled.append(replace(claim, enabled=enabled, default_on=claim.enabled))
        label = change_label(claim.kind, span, claim.names)
        changes.append(Change(change_id, claim.kind, span, label, enabled))
    draft.claims = settled
    return changes


def _same_feature(claim: Claim, title: Draft) -> Claim | None:
    """The title feature that credits every name `claim` credits, if there is one."""
    if claim.kind != "extract_feature":
        return None
    keys = {name_key(name) for name in claim.names}
    for other in title.claims:
        if other.kind == "extract_feature" and keys <= {name_key(name) for name in other.names}:
            return other
    return None


def _new_id(kind: str, span: str, taken: dict[str, int]) -> str:
    """A change id made from its kind and text, so it stays the same when other changes flip."""
    base = f"{kind}:{span}"
    taken[base] = taken.get(base, 0) + 1
    return base if taken[base] == 1 else f"{base}#{taken[base]}"


def _caps_span(draft: Draft) -> str | None:
    """The title as the default changes leave it, when it's all in capitals.

    A title that's nothing but promo or credits, like "[FREE DL]" alone, isn't offered.
    """
    own_words = rules.tidy(" ".join(assemble(draft, cut=lambda claim: True)))
    kept = rules.tidy(" ".join(assemble(draft, cut=lambda claim: claim.enabled)))
    return kept if own_words and rules.is_all_caps(kept) else None


def _caps_change(
    span: str | None, toggles: Mapping[str, bool], taken: dict[str, int]
) -> Change | None:
    """Offer title case for an all-caps title. It starts off, and never touches artist names."""
    if span is None:
        return None
    change_id = _new_id("fix_caps", span, taken)
    label = change_label("fix_caps", span)
    return Change(change_id, "fix_caps", span, label, toggles.get(change_id, False))


def _title_and_version(draft: Draft, words: Wordlists, *, caps_on: bool) -> tuple[str, str | None]:
    """The base title and version, with the changes that are on applied."""
    carried = artists.carried_features(draft)

    def cut(claim: Claim) -> bool:
        # A feature inside a taken artist part leaves the title with it, on or off.
        return claim.enabled or claim.start in carried

    def transform(text: str) -> str:
        return rules.title_case(text, words.caps_keep) if caps_on else text

    title, version = assemble(draft, cut=cut, transform=transform)
    # The version keeps the space before it, or the lack of one, as written.
    spaced = not title or title[-1].isspace() or version[:1].isspace()
    base_title = rules.tidy(title)
    kept_version = rules.tidy_version(version, spaced=spaced)
    # A title that is only a version, like "(Intro)", keeps it as the title.
    if not base_title and kept_version:
        return kept_version.strip(), None
    return base_title, kept_version


def _names(draft: Draft, kind: str) -> list[str]:
    """The artists credited by the claims of `kind` that are on."""
    return [
        name
        for claim in draft.claims
        if claim.kind == kind and claim.enabled
        for name in claim.names
    ]


def _name(draft: Draft, *, cut_all: bool) -> str:
    """An artist name, without its feat. part or emoji when those changes are on."""
    title, _version = assemble(draft, cut=lambda claim: cut_all or claim.enabled)
    return rules.tidy(title)
