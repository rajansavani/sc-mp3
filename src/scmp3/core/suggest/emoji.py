"""Real emoji, found by Unicode's emoji properties, and the step 7 rule that offers to remove them.

`emoji-data.txt` is Unicode's file, copied unchanged from
https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-data.txt (Emoji 17.0).
"""

import functools
import re
from collections.abc import Iterator
from dataclasses import replace
from importlib import resources

from scmp3.core.suggest.draft import VERSION, Claim, Context, Draft

# The removed text an emoji can decorate: promo, hashtags and the user's own words.
NOISE_KINDS = ("remove_promo", "custom_word")

# Each line names a code point or a range, then a property: "1F600..1F64F ; Emoji_Presentation".
_DATA_LINE = re.compile(r"^([0-9A-F]+)(?:\.\.([0-9A-F]+))?\s*;\s*(\w+)")
_TEXT_STYLE = "\ufe0e"
_EMOJI_STYLE = "\ufe0f"
_ZWJ = "\u200d"
# A digit, "#" or "*" is an emoji only as a keycap, with U+20E3 after it.
_KEYCAP = "[0-9#*]\ufe0f?\u20e3"
_FLAG = "[\U0001f1e6-\U0001f1ff]{2}"
_SKIN_TONE = "[\U0001f3fb-\U0001f3ff]"
# Tag letters and a cancel tag, which make the flags of England, Scotland and Wales.
_TAGS = "[\U000e0020-\U000e007e]+\U000e007f"


@functools.cache
def _ranges() -> dict[str, list[tuple[int, int]]]:
    """The code point ranges of each property in `emoji-data.txt`."""
    source = resources.files(__package__).joinpath("emoji-data.txt")
    found: dict[str, list[tuple[int, int]]] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        match = _DATA_LINE.match(line)
        if match:
            first, last, prop = match.groups()
            found.setdefault(prop, []).append((int(first, 16), int(last or first, 16)))
    return found


def _char_class(ranges: list[tuple[int, int]]) -> str:
    """A regex character class for code point ranges, leaving out ASCII."""
    parts = [f"\\U{max(first, 0x80):08x}-\\U{last:08x}" for first, last in ranges if last >= 0x80]
    return "[" + "".join(parts) + "]"


@functools.cache
def emoji_run() -> re.Pattern[str]:
    """A run of emoji, with spaces between them allowed, so "🔥 🔥" is one run."""
    ranges = _ranges()
    emoji = _char_class(ranges["Emoji"])
    # Characters shown as emoji by default, like 🔥. Others, like ✔ or ™, are emoji only with
    # U+FE0F after them, and symbols with no emoji property, like ✧ ★ ♡ ♪, never are.
    shown_as_emoji = _char_class(ranges["Emoji_Presentation"])
    takes_skin_tone = _char_class(ranges["Emoji_Modifier_Base"])
    core = (
        f"(?:{_KEYCAP}|{_FLAG}|{takes_skin_tone}{_SKIN_TONE}|{emoji}{_EMOJI_STYLE}"
        f"|{shown_as_emoji}(?!{_TEXT_STYLE}))"
    )
    # Each part of a sequence like 👩‍❤️‍👨 after a joiner may leave out U+FE0F.
    joined = f"(?:{_ZWJ}(?:{core}|{emoji}){_EMOJI_STYLE}?{_SKIN_TONE}?)"
    one = f"{core}{_EMOJI_STYLE}?{_SKIN_TONE}?(?:{_TAGS})?{joined}*"
    return re.compile(f"{one}(?:\\s*{one})*")


def find_emoji(text: str) -> Iterator[tuple[int, int]]:
    """Where each run of real emoji is in `text`, as (start, end)."""
    for match in emoji_run().finditer(text):
        yield match.span()


def claim_emoji(draft: Draft, context: Context) -> None:
    """Step 7, early: each run of emoji at an edge, so the rules after it can see past them.

    Emoji on both sides of a title make two changes. Whether each starts on is settled last.
    """
    masked = draft.masked()
    for start, end in _unclaimed_emoji(draft):
        # A run between two words waits for `claim_remaining_emoji`, after the other rules.
        if not _between_words(masked, start, end):
            draft.claim("remove_emoji", start, end, enabled=False)


def claim_remaining_emoji(draft: Draft, context: Context) -> None:
    """Step 7, once the other rules have claimed their text: every emoji left."""
    for start, end in _unclaimed_emoji(draft):
        draft.claim("remove_emoji", start, end, enabled=False)


def _unclaimed_emoji(draft: Draft) -> Iterator[tuple[int, int]]:
    """The emoji runs outside claimed text. A run stops at a claim, so it never spans one."""
    return find_emoji(_without(draft.text, draft.claims, filler="\x00"))


def settle_emoji(draft: Draft, context: Context) -> None:
    """Emoji stay, and their removal is offered switched off, unless they only decorated promo or
    noise text that's removed. So "Paper Moon 🔥 FREE DL 🔥" gives "Paper Moon", but the emoji
    next to a feat., prod. or artist credit stays.
    """
    noise = [claim for claim in draft.claims if claim.enabled and claim.kind in NOISE_KINDS]
    gone = [claim for claim in draft.claims if _leaves_title(claim)]
    kept = _without(draft.text, gone)
    draft.claims = [
        _settled(claim, draft.text, kept, noise) if claim.kind == "remove_emoji" else claim
        for claim in draft.claims
    ]


def _settled(claim: Claim, text: str, kept: str, noise: list[Claim]) -> Claim:
    """An emoji claim, on when it touches removed noise and doesn't sit between kept words."""
    touches = any(_touching(text, claim, other) for other in noise)
    on = touches and not _between_words(kept, claim.start, claim.end)
    return replace(claim, enabled=on)


def _leaves_title(claim: Claim) -> bool:
    """Whether a claim takes its text out of the title by default."""
    return claim.enabled and claim.kind not in (VERSION, "remove_emoji")


def _touching(text: str, emoji: Claim, other: Claim) -> bool:
    """Whether only spaces lie between an emoji claim and another claim."""
    if other.end <= emoji.start:
        return not text[other.end : emoji.start].strip()
    if emoji.end <= other.start:
        return not text[emoji.end : other.start].strip()
    return False


def _without(text: str, claims: list[Claim], filler: str = " ") -> str:
    """`text` with each claim's region filled with `filler`. Offsets stay put."""
    chars = list(text)
    for claim in claims:
        chars[claim.start : claim.end] = filler * (claim.end - claim.start)
    return "".join(chars)


def _between_words(text: str, start: int, end: int) -> bool:
    """Whether text[start:end] has a letter or digit on each side, spaces aside."""
    before, after = text[:start].rstrip(), text[end:].lstrip()
    return bool(before and after) and before[-1].isalnum() and after[0].isalnum()
