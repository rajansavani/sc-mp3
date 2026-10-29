"""Builds a TagSuggestion: runs the rules over the title in order, then settles each change."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from scmp3.core.models import Change, Settings, SourceInfo, TagSuggestion
from scmp3.core.suggest import (
    artist_end,
    artist_lists,
    artists,
    emoji,
    file_names,
    producers,
    rules,
    title_text,
    youtube,
)
from scmp3.core.suggest.assembly import assemble
from scmp3.core.suggest.draft import VERSION, Claim, Context, Draft
from scmp3.core.suggest.labels import change_label, feature_label, new_change_id, shown_text
from scmp3.core.suggest.names import any_same_artist, split_artist_list, strip_invisible
from scmp3.core.suggest.words import Wordlists, load_wordlists

Step = Callable[[Draft, Context], None]

# The title rules in the order they claim text. An earlier claim wins where two rules overlap.
TITLE_STEPS: tuple[Step, ...] = (
    rules.claim_bracket_versions,  # first, since keep-list words are never removed
    rules.claim_bracket_promo,  # before features, so "(With Lyrics)" isn't a feature
    artists.claim_bracket_features,
    rules.claim_bracket_producers,
    # After producers, so "(feat. Lou V (prod. Hazy))" leaves the producer credit its own change.
    artists.claim_nested_features,
    rules.claim_leading_promo,
    artists.claim_pipe_artist,
    rules.claim_pipe_segment,
    rules.claim_trailing_promo,
    emoji.claim_emoji,
    rules.claim_inline_producers,
    artist_end.claim_artist_at_end,  # before the dash split, so "Numb - Linkin Park" isn't one
    artists.claim_title_artist,
    artists.claim_inline_features,
    rules.claim_custom_words,
    # Again, for promo that only ends the title once a feature is claimed: "Song FREE DL ft. X".
    rules.claim_trailing_promo,
    emoji.claim_remaining_emoji,  # last, once everything around each emoji is claimed
)
# The rules for a publisher artist name such as "Kaia feat. Lou V".
ARTIST_FIELD_STEPS: tuple[Step, ...] = (
    artists.claim_bracket_features,
    artists.claim_nested_features,
    artists.claim_inline_features,
)
# The rules for the uploader's name, which becomes the artist when nothing else names one.
UPLOADER_STEPS: tuple[Step, ...] = (emoji.claim_emoji, emoji.settle_emoji)
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
    caps_span = title_text.caps_span(drafts.title)
    changes = _changes(drafts, toggles)
    credits = _producer_changes(drafts, toggles)
    changes.extend(credits)
    caps = title_text.caps_change(caps_span, toggles, drafts.taken)
    if caps:
        changes.append(caps)
    caps_on = bool(caps and caps.enabled)
    base_title, version = title_text.title_and_version(drafts.title, words, caps_on=caps_on)
    main, featured = _artists(drafts, held=producers.held_back(credits))
    changes = _feature_labels(changes, drafts, main, featured)
    when = source.release_date or source.upload_date
    return TagSuggestion(
        base_title=base_title,
        main_artists=main,
        featured=featured,
        version=version,
        album=strip_invisible(source.album or "") or None,
        year=when.year if when else None,
        genre=(source.genre or "").strip().lstrip("#").strip() or None,
        changes=changes,
    )


@dataclass
class _Drafts:
    """The drafts of one source's title, publisher artists and uploader name."""

    context: Context
    title: Draft
    credits: list[Draft]
    uploader: Draft
    # The uploader's emoji change only shows when the uploader is the artist to begin with.
    uploader_is_artist: bool
    taken: dict[str, int] = field(default_factory=dict)  # change ids used so far
    # The names each feature change credits, by change id, spelled as the tags spell them.
    feature_names: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _detect(source: SourceInfo, settings: Settings, words: Wordlists) -> _Drafts:
    """Run the rules on every text, with each claim at its default."""
    context = Context(source, settings, words)
    uploader = _run(youtube.uploader_name(source), UPLOADER_STEPS, context)
    credits = [_run(name, ARTIST_FIELD_STEPS, context) for name in source.artists]
    credits = [credit for credit in credits if credit.text]
    publisher = [_listed_names(_name(credit, cut_all=True), context) for credit in credits]
    context = replace(
        context,
        publisher=tuple(name for names in publisher for name in names),
        uploader=_name(uploader, cut_all=True),
    )
    title = _run(file_names.title_to_read(source), TITLE_STEPS, context)
    _apply_settings(title, settings)
    # Last, so an emoji next to a credit the settings keep stays too.
    emoji.settle_emoji(title, context)
    _keep_title_from_emptying(title)
    uploader_is_artist = not credits and not artist_lists.title_artists(title)
    return _Drafts(context, title, credits, uploader, uploader_is_artist)


def _listed_names(name: str, context: Context) -> list[str]:
    """The names a publisher artist gives. SoundCloud's is free text, so a list in it is split."""
    if context.source.kind != "soundcloud":
        return [name] if name else []
    return split_artist_list(name, context.words.whole_names)


def _changes(drafts: _Drafts, toggles: Mapping[str, bool]) -> list[Change]:
    """Settle the title's changes, then the publisher artists' and the uploader's."""
    spell = _credited_spelling(drafts)
    changes = _settle(drafts.title, toggles, drafts, spell)
    for credit in drafts.credits:
        changes.extend(_settle(credit, toggles, drafts, spell, follow=drafts.title))
    if drafts.uploader_is_artist:
        changes.extend(_settle(drafts.uploader, toggles, drafts, spell))
    return changes


def _producer_changes(drafts: _Drafts, toggles: Mapping[str, bool]) -> list[Change]:
    """A change for each listed producer that would be featured, crediting them as featured."""
    featured = _publisher(drafts).featured
    context = drafts.context
    marked = producers.marked_producers(
        featured, drafts.title, context.source.description, context.words
    )
    # A name the title also features or takes as its artist, as in "(feat. Kaia) (prod. Kaia)",
    # stays credited.
    credited = [name for names in drafts.feature_names.values() for name in names]
    credited += [artist.base for artist in artist_lists.title_artists(drafts.title)]
    held = [name for name in marked if not any_same_artist([name], credited)]
    return producers.producer_changes(held, toggles, drafts.taken)


def _feature_labels(
    changes: list[Change], drafts: _Drafts, main: list[str], featured: list[str]
) -> list[Change]:
    """Each feature's change labelled with what it did, once the artists are known."""
    labelled: list[Change] = []
    for change in changes:
        names = drafts.feature_names.get(change.id)
        if names:
            span = rules.balance_brackets(change.span)
            change = replace(change, label=feature_label(span, names, main, featured))
        labelled.append(change)
    return labelled


def _credited_spelling(drafts: _Drafts) -> Callable[[str], str]:
    """How the tags spell a featured name, for its toggle label, whichever changes are on."""
    publisher = list(drafts.context.publisher)
    publisher += [
        name
        for credit in drafts.credits
        for claim in credit.claims
        if claim.kind == "extract_feature"
        for name in claim.names
    ]
    uploader = _name(drafts.uploader, cut_all=True)
    return lambda name: artist_lists.credited_spelling(name, publisher, uploader)


def _publisher(drafts: _Drafts) -> artist_lists.PublisherArtists:
    """The publisher's artists as main and featured names, given which changes are on."""
    uploader = _name(drafts.uploader, cut_all=not drafts.uploader_is_artist)
    names = [_name(credit, cut_all=False) for credit in drafts.credits]
    lists = [_listed_names(name, drafts.context) for name in names]
    from_title = artist_lists.title_artists(drafts.title)
    words = drafts.context.words
    named_producers = [
        name for names in lists for name in names if producers.is_producer_credit(name, words)
    ]
    uploaders = [drafts.uploader.text, uploader]
    return artist_lists.publisher_artists(lists, from_title, uploaders, named_producers)


def _artists(drafts: _Drafts, held: list[str]) -> tuple[list[str], list[str]]:
    """Step 9: the main and featured artists, given which changes are on.

    `held` has the listed producers whose credit is off.
    """
    uploader = _name(drafts.uploader, cut_all=not drafts.uploader_is_artist)
    from_title = artist_lists.title_artists(drafts.title)
    publisher = _publisher(drafts)
    # A listed name whose feat. text a toggle put back in the title isn't credited twice.
    put_back = [
        name
        for claim in drafts.title.claims
        if claim.kind == "extract_feature" and not claim.enabled
        for name in claim.names
    ]
    listed = [
        name for name in publisher.featured if not any_same_artist([name], [*put_back, *held])
    ]
    featured = artist_lists.merge_featured(
        listed + _names(drafts.title, "extract_feature"),
        [name for credit in drafts.credits for name in _names(credit, "extract_feature")],
        uploader,
    )
    main = artist_lists.choose_main(publisher.main, from_title, uploader, publisher.listed)
    return artist_lists.settle(main, featured)


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
    draft: Draft,
    toggles: Mapping[str, bool],
    drafts: _Drafts,
    spell: Callable[[str], str],
    follow: Draft | None = None,
) -> list[Change]:
    """Turn a draft's claims into changes, in text order, switched as `toggles` says.

    `spell` gives each credited name in its labels the spelling the tags use. With `follow`, a
    feature that the followed draft already credits shares its change. The ids used so far and
    each feature's names are kept in `drafts`.
    """
    settled: list[Claim] = []
    changes: list[Change] = []
    for claim in sorted(draft.claims, key=lambda claim: claim.start):
        leader = _same_feature(claim, follow) if follow else None
        if claim.kind == VERSION or leader:
            settled.append(replace(claim, enabled=leader.enabled) if leader else claim)
            continue
        span = draft.span(claim)
        change_id = new_change_id(claim.kind, shown_text(claim.kind, span), drafts.taken)
        enabled = toggles.get(change_id, claim.enabled)
        settled.append(replace(claim, enabled=enabled, default_on=claim.enabled))
        # A credit cut short by a version, like "(prod. Hazy", shows its bracket closed.
        names = tuple(spell(name) for name in claim.names)
        if claim.kind == "extract_feature":
            drafts.feature_names[change_id] = names
        label = change_label(claim.kind, rules.balance_brackets(span), names)
        changes.append(Change(change_id, claim.kind, span, label, enabled))
    draft.claims = settled
    return changes


def _same_feature(claim: Claim, title: Draft) -> Claim | None:
    """The title feature that credits every name `claim` credits, if there is one."""
    if claim.kind != "extract_feature":
        return None
    for other in title.claims:
        if other.kind != "extract_feature":
            continue
        # "Zoë" in the artist field and "Zoe" in the title are one feature.
        if all(any_same_artist([name], other.names) for name in claim.names):
            return other
    return None


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
