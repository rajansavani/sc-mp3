"""The tags a track is saved with: the suggestion in a title style with the hand edits, plus what
comes from the source itself."""

from collections.abc import Callable, Mapping
from dataclasses import replace

from scmp3.core.models import CommentMode, Settings, SourceInfo, Tags, TagSuggestion, TitleStyle
from scmp3.core.suggest import InvalidOverride, render

# What each number tag may hold. tagger.write refuses anything else as a bug, so it's checked here.
NUMBER_RANGES: dict[str, range] = {
    "year": range(1, 10_000),
    "track_no": range(1, 1000),
    "track_total": range(1, 1000),
}

# What goes in the comment tag for each comment_mode setting.
COMMENTS: dict[CommentMode, Callable[[SourceInfo], str | None]] = {
    "source_url": lambda source: source.url,
    "description": lambda source: source.description,
    "none": lambda source: None,
}


def render_tags(
    source: SourceInfo,
    suggestion: TagSuggestion,
    settings: Settings,
    *,
    style: TitleStyle | None = None,
    overrides: Mapping[str, str] | None = None,
) -> Tags:
    """The tags to write: the suggestion in a title style with the hand edits, plus source details.

    Raises InvalidOverride for a typed number that isn't one, or that no tag can hold.
    """
    tags = render(suggestion, style, overrides or {}, settings)
    _check_numbers(tags)
    if source.kind == "file":
        # A file keeps its own comment and link, which retag reads again as it writes.
        return replace(tags, comment=source.description, isrc=source.isrc, label=source.label)
    return replace(
        tags,
        comment=COMMENTS[settings.comment_mode](source),
        source_url=source.url,
        source_id=source.id,
        isrc=source.isrc,
        label=source.label,
    )


def _check_numbers(tags: Tags) -> None:
    """Raise InvalidOverride for a year, track or total outside its range."""
    for name, allowed in NUMBER_RANGES.items():
        value = getattr(tags, name)
        if value is not None and value not in allowed:
            raise InvalidOverride(name, str(value))
    if tags.track_no and tags.track_total and tags.track_total < tags.track_no:
        raise InvalidOverride("track_total", str(tags.track_total))
