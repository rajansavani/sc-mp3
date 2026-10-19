"""The pipeline's last stage: add the cover and tags to the staged file, move it into the library
and record it in the history."""

import logging
from collections.abc import Mapping
from pathlib import Path

from scmp3.core import convert, tagger
from scmp3.core.artwork import Cover
from scmp3.core.cover_choice import load_cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.library import AskFn, plan_destination, resolve_collision
from scmp3.core.models import (
    ArtworkChoice,
    HistoryEntry,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.pipeline.common import (
    Prepared,
    Saved,
    Staged,
    discard,
    expected_duration,
    only_scmp_errors,
)
from scmp3.core.placing import atomic_place

log = logging.getLogger(__name__)


def destination(
    source: SourceInfo,
    suggestion: TagSuggestion,
    staged: Staged,
    tags: Tags,
    settings: Settings,
    *,
    style: TitleStyle | None = None,
    overrides: Mapping[str, str] | None = None,
) -> Path:
    """Where finalize saves the file, before checking whether the name is taken.

    Pass the suggestion, style and overrides the tags were rendered from. Album subfolders use
    them to tell a single's album from a real one (TECH_SPEC §12).
    """
    typed_album = "album" in (overrides or {})
    return plan_destination(
        settings.library_dir,
        tags,
        settings,
        staged.fmt,
        style=style,
        album_from_singles_setting=suggestion.album is None and not typed_album,
        source_titles=(suggestion.base_title, source.title),
    )


def finalize(
    prepared: Prepared,
    staged: Staged,
    tags: Tags,
    cover_choice: ArtworkChoice,
    settings: Settings,
    *,
    style: TitleStyle | None = None,
    suggestion: TagSuggestion | None = None,
    overrides: Mapping[str, str] | None = None,
    ask: AskFn | None = None,
) -> Saved | None:
    """Add the cover and tags, check the file, move it into the library and record it.

    `style`, `suggestion` and `overrides` are what the tags were rendered from; the suggestion
    defaults to prepare's. A cover that can't be downloaded or read is left out, and `Saved` says
    why. Returns None when the name is taken and the when_file_exists setting, or `ask` under
    "ask", says to skip. Either way the staging folder is deleted. Whatever `ask` raises reaches
    the caller unchanged.
    """
    reviewed = suggestion or prepared.suggestion
    # The staged file is still in sc-mp3's own folders, so disk errors here are about them.
    with only_scmp_errors(where="data"):
        cover_error = _tag_staged(prepared, staged, tags, cover_choice, settings)
    with only_scmp_errors(where="library"):
        planned = destination(
            prepared.source, reviewed, staged, tags, settings, style=style, overrides=overrides
        )
    # Outside the error mapping, since `ask` is the caller's and its errors are theirs.
    dest = resolve_collision(planned, settings.when_file_exists, ask)
    if dest is None:
        log.info("Didn't save %s, since the name is taken", planned.name)
        discard(prepared)
        return None
    with only_scmp_errors(where="library"):
        atomic_place(staged.path, dest)
    log.info("Saved %s", dest.name)
    _record(prepared.tools.history, prepared.source, staged, tags, dest)
    discard(prepared)
    return Saved(dest, cover_error)


def _tag_staged(
    prepared: Prepared,
    staged: Staged,
    tags: Tags,
    cover_choice: ArtworkChoice,
    settings: Settings,
) -> ScmpError | None:
    """Write the tags and cover to the staged file and check it. Returns why a cover is missing."""
    cover, cover_error = _cover_if_it_loads(prepared, cover_choice, settings)
    tagger.write(staged.path, tags, cover, fmt=staged.fmt)
    convert.validate(staged.path, expected_duration(prepared.source))
    return cover_error


def _cover_if_it_loads(
    prepared: Prepared, choice: ArtworkChoice, settings: Settings
) -> tuple[Cover | None, ScmpError | None]:
    """The cover, or None with the reason when it can't be downloaded or read."""
    try:
        with prepared.tools.make_ydl() as ydl:
            cover = load_cover(choice, prepared.source, settings.cover_max_size, ydl.urlopen)
    except ScmpError as error:
        # A bug in sc-mp3 still fails the save; only a cover that won't load is left out.
        if error.kind == ErrorKind.internal:
            raise
        log.warning("Saving without a cover, since it couldn't be loaded: %s", error)
        return None, error
    return cover, None


def _record(history: History, source: SourceInfo, staged: Staged, tags: Tags, dest: Path) -> None:
    """Add the save to the history. A failure only warns, since the file is already saved."""
    entry = HistoryEntry(
        source_id=source.id,
        url_norm=source.url,
        saved_path=dest,
        title=tags.title,
        artist=tags.artist,
        album=tags.album,
        format=staged.fmt,
        bitrate=staged.audio.bitrate,
    )
    try:
        history.add(entry)
    except ScmpError as error:
        log.warning("The save wasn't added to the history, so it can't warn of repeats: %s", error)
