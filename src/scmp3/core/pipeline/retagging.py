"""Re-tagging a file you already have: read it like a source, then write the reviewed tags into it.

Nothing is downloaded. Only the fields the editor changed are rewritten, in a hidden copy next to
the file that's swapped in once it's complete, so a failure at any point leaves the file as it was
(TECH_SPEC §12).
"""

import logging
import uuid
from pathlib import Path

from scmp3.core import artwork, tagger
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import ArtworkChoice, HistoryEntry, Settings, Tags
from scmp3.core.pipeline.common import Prepared, Saved, Tools, discard, only_scmp_errors
from scmp3.core.pipeline.saving import LoadedCover, try_load_cover
from scmp3.core.placing_retag import retag_in_place
from scmp3.core.sources import localfile
from scmp3.core.sources.localfile import LocalFile

log = logging.getLogger(__name__)


def prepare_file(
    parsed: ParsedInput,
    *,
    settings: Settings,
    tools: Tools | None = None,
    job_id: str | None = None,
) -> Prepared:
    """Read a file's tags and cover, and suggest cleaned-up tags with the usual defaults.

    The file is looked at here, on the caller's worker, since a network path can be slow. Raises
    ScmpError with the reason file_missing or unreadable_file when it can't be re-tagged.
    """
    if parsed.kind != "local_file" or parsed.path is None:
        raise ValueError(f"prepare_file takes a local file, not a {parsed.kind} input")
    with only_scmp_errors(where="file"):
        local = localfile.read_file(parsed.path)
    return Prepared(
        job_id=job_id or uuid.uuid4().hex,
        source=local.source,
        suggestion=localfile.suggestion_for(local, settings),
        # A file you already have isn't a repeat of itself.
        duplicate=None,
        settings=settings,
        info={},
        tools=tools or Tools(),
    )


def retag(prepared: Prepared, tags: Tags, cover_choice: ArtworkChoice, settings: Settings) -> Saved:
    """Write the reviewed tags and cover into the file, then update its history entry.

    Only the fields that differ from the file as it is now are rewritten, so everything else in
    it, from the source link to lyrics and ReplayGain, stays as it was. "source" with no crop
    keeps the file's own cover byte for byte. A new cover that can't be loaded leaves the old one
    in place, and `Saved` says why. Raises ScmpError, with the file untouched.
    """
    path = Path(prepared.source.id)
    with only_scmp_errors(where="file"):
        local = localfile.read_file(path)
        localfile.check_unchanged(prepared.source, local)
        fields = changed_fields(tags, local.tags)
        cover = _new_cover(prepared.tools, cover_choice, local, settings)
        # A new cover that failed to load leaves the old one, rather than none.
        replace_cover = cover is not None and cover.error is None
        # With nothing to change, the file isn't touched at all, not even rewritten as it was.
        if fields or replace_cover:
            _write(
                path,
                tags,
                fields,
                cover.cover if replace_cover and cover else None,
                local,
                replace_cover,
            )
    log.info("Re-tagged %s", path.name)
    _record(prepared.tools.history, local, tags)
    # The staging folder holds only a copy of the cover, if anything.
    discard(prepared)
    return Saved(path, cover.error if cover else None)


def changed_fields(tags: Tags, current: Tags) -> list[str]:
    """The fields the editor changes whose new value differs from the file's."""
    return [name for name in tagger.EDITED_FIELDS if getattr(tags, name) != getattr(current, name)]


def replaces_cover(choice: ArtworkChoice) -> bool:
    """Whether the cover was changed in the editor: another image, none, or a crop of its own."""
    return choice.kind != "source" or choice.crop is not None


def _new_cover(
    tools: Tools, choice: ArtworkChoice, local: LocalFile, settings: Settings
) -> LoadedCover | None:
    """The cover to put in the file, or None to keep the one it has.

    A crop of the file's own cover is processed like any other cover.
    """
    if not replaces_cover(choice):
        return None
    if choice.kind != "source":
        return try_load_cover(tools, choice, local.source, settings)
    if local.cover is None:
        return LoadedCover(None)
    try:
        cover = artwork.process(local.cover, crop=choice.crop, max_size=settings.cover_max_size)
    except ScmpError as error:
        if error.kind == ErrorKind.internal:
            raise
        log.warning("The file's cover can't be cropped, so it stays as it is: %s", error)
        return LoadedCover(None, error)
    return LoadedCover(cover)


def _write(
    path: Path,
    tags: Tags,
    fields: list[str],
    cover: Cover | None,
    local: LocalFile,
    replace_cover: bool,
) -> None:
    """Update a hidden copy and swap it in. A file that went meanwhile is reported as missing."""

    def update(copy: Path) -> None:
        # The copy's name ends in .tmp, so the format is passed rather than read from it.
        tagger.update(
            copy, tags, fields=fields, cover=cover, replace_cover=replace_cover, fmt=local.fmt
        )

    try:
        retag_in_place(path, update)
    except ScmpError as error:
        if error.reason is None and error.kind == ErrorKind.convert_failed:
            # mutagen refused the copy's tag, which says nothing about converting audio.
            error.reason = "tag_write_failed"
        if error.reason is None and not path.is_file():
            raise ScmpError(
                ErrorKind.disk, f"{path.name} went while it was re-tagged", reason="file_missing"
            ) from error
        raise


def _record(history: History, local: LocalFile, tags: Tags) -> None:
    """Update the file's history entries, or add one for a file sc-mp3 didn't save. A failure
    only warns, since the file is already re-tagged."""
    try:
        if history.update_tags(local.path, tags.title, tags.artist, tags.album) > 0:
            return
        history.add(
            HistoryEntry(
                # The file's own link and id, which a re-tag never changes.
                source_id=local.tags.source_id,
                url_norm=local.tags.source_url,
                saved_path=local.path,
                title=tags.title,
                artist=tags.artist,
                album=tags.album,
                format=local.fmt,
                bitrate=local.bitrate,
            )
        )
    except ScmpError as error:
        log.warning("The re-tag wasn't recorded in the history: %s", error)
