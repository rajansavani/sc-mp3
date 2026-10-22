"""The pipeline's last stage: add the cover and tags to the staged file, move it into the library
and record it in the history. saving_sets saves the tracks of a set with it."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scmp3.core import convert, tagger
from scmp3.core.artwork import Cover
from scmp3.core.cover_choice import load_cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.library import (
    AskFn,
    plan_destination,
    resolve_collision,
    reuse_folders_by_case,
)
from scmp3.core.models import (
    ArtworkChoice,
    HistoryEntry,
    PlaylistInfo,
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
    Tools,
    discard,
    expected_duration,
    only_scmp_errors,
)
from scmp3.core.placing import atomic_place

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedCover:
    """A cover ready for the tags, or None with the reason it couldn't be loaded."""

    cover: Cover | None
    error: ScmpError | None = None


def destination(
    source: SourceInfo,
    suggestion: TagSuggestion,
    staged: Staged,
    tags: Tags,
    settings: Settings,
    *,
    style: TitleStyle | None = None,
    overrides: Mapping[str, str] | None = None,
    in_album_set: bool = False,
) -> Path:
    """Where finalize saves the file, before it reuses folders by case and checks for a taken name.

    Pass the suggestion, style and overrides the tags were rendered from. Album subfolders use
    them to tell a single's album from a real one, and `in_album_set` marks a track saved from a
    set in album mode (TECH_SPEC §12). It doesn't read the disk.
    """
    typed_album = "album" in (overrides or {})
    return plan_destination(
        settings.library_dir,
        tags,
        settings,
        staged.fmt,
        style=style,
        in_album_set=in_album_set,
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
    in_album_set: bool = False,
) -> Saved | None:
    """Add the cover and tags, check the file, move it into the library and record it.

    `style`, `suggestion` and `overrides` are what the tags were rendered from; the suggestion
    defaults to prepare's. `in_album_set` marks a track of a set saved as an album, as for
    destination. A cover that can't be downloaded or read is left out, and `Saved` says why.
    Returns None when the name is taken and the when_file_exists setting, or `ask` under "ask",
    says to skip. Either way the staging folder is deleted. Whatever `ask` raises reaches the
    caller unchanged.
    """
    with only_scmp_errors(where="data"):
        cover = try_load_cover(prepared.tools, cover_choice, prepared.source, settings)
    return finalize_with_cover(
        prepared,
        staged,
        tags,
        cover,
        settings,
        style=style,
        suggestion=suggestion,
        overrides=overrides,
        ask=ask,
        in_album_set=in_album_set,
    )


def finalize_with_cover(
    prepared: Prepared,
    staged: Staged,
    tags: Tags,
    cover: LoadedCover,
    settings: Settings,
    *,
    style: TitleStyle | None = None,
    suggestion: TagSuggestion | None = None,
    overrides: Mapping[str, str] | None = None,
    ask: AskFn | None = None,
    in_album_set: bool = False,
) -> Saved | None:
    """finalize with the cover already loaded, as the tracks of an album share one."""
    reviewed = suggestion or prepared.suggestion
    # The staged file is still in sc-mp3's own folders, so disk errors here are about them.
    with only_scmp_errors(where="data"):
        tagger.write(staged.path, tags, cover.cover, fmt=staged.fmt)
        convert.validate(staged.path, expected_duration(prepared.source))
    with only_scmp_errors(where="library"):
        planned = destination(
            prepared.source,
            reviewed,
            staged,
            tags,
            settings,
            style=style,
            overrides=overrides,
            in_album_set=in_album_set,
        )
        # Here rather than in destination(), since it lists folders and previews shouldn't wait.
        planned = reuse_folders_by_case(planned, settings.library_dir)
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
    return Saved(dest, cover.error)


def try_load_cover(
    tools: Tools, choice: ArtworkChoice, owner: SourceInfo | PlaylistInfo, settings: Settings
) -> LoadedCover:
    """The cover, or None with the reason when it can't be downloaded or read.

    `owner` is the track or set whose own artwork a "source" choice means. A bug in sc-mp3, an
    internal error, still raises.
    """
    try:
        with tools.make_ydl() as ydl:
            cover = load_cover(choice, owner, settings.cover_max_size, ydl.urlopen)
    except ScmpError as error:
        # A bug in sc-mp3 still fails the save; only a cover that won't load is left out.
        if error.kind == ErrorKind.internal:
            raise
        log.warning("Saving without a cover, since it couldn't be loaded: %s", error)
        return LoadedCover(None, error)
    return LoadedCover(cover)


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
