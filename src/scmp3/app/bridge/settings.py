"""Bridge calls for the options menu and first-run setup: saving settings and live previews.

`update_settings` checks each value more closely than the settings store does, so the UI can say
next to each field what's wrong. `preview_render` runs a sample track through the real suggestion
engine and library rules.
"""

import logging
import os
import re
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, get_args

from scmp3.app.context import AppContext
from scmp3.core import library, paths
from scmp3.core.errors import ScmpError
from scmp3.core.models import (
    AudioFormat,
    Settings,
    SourceInfo,
    TagSuggestion,
    TitleStyle,
    field_types,
)
from scmp3.core.settings import InvalidSettings, invalid_keys
from scmp3.core.suggest import render, suggest
from scmp3.core.suggest.render import token_values

INVALID_SETTINGS = "invalid_settings"
INVALID_SAMPLE = "invalid_sample"
INVALID_STYLE = "invalid_style"

# Why a setting wasn't saved. The UI gets these codes, and web/strings/settings.js has the text.
UNKNOWN_SETTING = "UNKNOWN_SETTING"
INVALID_VALUE = "INVALID_VALUE"
NOT_ABSOLUTE = "NOT_ABSOLUTE"
NOT_A_FOLDER = "NOT_A_FOLDER"
CANT_CREATE = "CANT_CREATE"
NOT_WRITABLE = "NOT_WRITABLE"
UNKNOWN_TOKEN = "UNKNOWN_TOKEN"
FEAT_TOKEN = "FEAT_TOKEN"
OPEN_BRACE = "OPEN_BRACE"
NO_TOKENS = "NO_TOKENS"

LIBRARY_DIR = "library_dir"
FILENAME_TEMPLATE = "filename_template"

# A problem with one setting: {"reason": <code>}, plus "token" for an unknown token, or
# "folder" for a library folder that can't be used.
Problem = dict[str, str]
Probe = Callable[[Path], None]

# Anything in braces. In a template, braces are only for tokens.
BRACED = re.compile(r"\{[^{}]*\}")
# The tokens of the title and artist templates, named the way the suggestion engine fills them in.
TITLE_TOKENS = frozenset(token_values(TagSuggestion(base_title="", main_artists=[]), ""))
# People expect this token, but the custom templates leave the wording around {featured} to them.
FEAT_WORD = "{feat}"
# No path can hold this character, and Path raises on it.
NULL = "\x00"
# The start of the name of the file that checks whether a folder can be written to.
PROBE_PREFIX = ".sc-mp3-write-check-"

# The design's sample track, which previews show unless the caller passes parts of its own.
SAMPLE_URL = "https://soundcloud.com/kaia/midnight-drive"
SAMPLE: dict[str, Any] = {
    "title": "Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
    "uploader": "Kaia",
    "artists": [],
}
SAMPLE_DATE = date(2026, 3, 14)
SAMPLE_GENRE = "Hip-hop"
# The sample is an AAC stream, so the m4a format keeps it as .m4a.
PREVIEW_EXTENSIONS: dict[AudioFormat, str] = {"mp3_320": "mp3", "mp3_v0": "mp3", "m4a": "m4a"}

log = logging.getLogger(__name__)


class SettingsApi:
    """The settings calls."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """The settings, and whether first-run setup still has to run."""
        settings = self._context.settings.load()
        return {"settings": settings.to_dict(), "first_run": not settings.first_run_done}

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Check a patch of setting names and values, and save it. Saves nothing if any is bad.

        Returns the saved settings, or `invalid_settings` with a problem per field, or the kind
        of a ScmpError, such as `disk` when settings.json can't be written.
        """
        if not _is_patch(patch):
            return {"error": INVALID_SETTINGS, "fields": {}}
        patch = _normalized(patch, self._context.settings.load())
        problems = patch_problems(patch)
        # The folder check creates a missing folder, so it waits until the rest is fine.
        if not problems and LIBRARY_DIR in patch:
            folder_problem = library_dir_problem(patch[LIBRARY_DIR])
            if folder_problem:
                problems[LIBRARY_DIR] = folder_problem
        if problems:
            return {"error": INVALID_SETTINGS, "fields": problems}
        try:
            saved = self._context.settings.update(patch)
        except InvalidSettings as error:
            # The checks above should have caught it, so the store's own check is the last word.
            log.warning("The settings store refused settings the bridge accepted: %s", error)
            fields = {key: {"reason": INVALID_VALUE} for key in error.keys}
            return {"error": INVALID_SETTINGS, "fields": fields}
        except ScmpError as error:
            log.warning("The settings weren't saved: %s", error)
            return {"error": error.kind.value}
        return {"settings": saved.to_dict()}

    def preview_render(
        self, sample: dict[str, Any] | None = None, style: str | None = None
    ) -> dict[str, Any]:
        """The title, artist and file name a sample track gets, for live previews.

        `sample` may replace the design track's `title`, `uploader` or `artists`. `style` is a
        title style id, or None for the style setting. The file name is relative to the library
        folder. The sample is a single, so it never goes in an album folder.
        """
        source = sample_source(sample)
        if source is None:
            return {"error": INVALID_SAMPLE}
        if style is not None and style not in get_args(TitleStyle):
            return {"error": INVALID_STYLE}
        settings = self._context.settings.load()
        suggestion = suggest(source, settings)
        tags = render(suggestion, style, {}, settings)
        extension = PREVIEW_EXTENSIONS[settings.audio_format]
        try:
            path = library.plan_destination(
                settings.library_dir,
                tags,
                settings,
                extension,
                style=style,
                # With no album from the source and none typed, the singles setting chose it.
                album_from_singles_setting=suggestion.album is None,
                source_titles=(suggestion.base_title, source.title),
            )
        except ScmpError as error:
            log.info("No file name preview: %s", error)
            return {"error": error.kind.value}
        return {
            "title": tags.title,
            "artist": tags.artist,
            "filename": path.relative_to(settings.library_dir).as_posix(),
            "filename_template": library.filename_template(settings, style),
        }


def _is_patch(patch: Any) -> bool:
    """Whether the UI sent a dict of setting names."""
    return isinstance(patch, dict) and all(isinstance(key, str) for key in patch)


def _normalized(patch: Mapping[str, Any], current: Settings) -> dict[str, Any]:
    """The patch with no file name template in place of an empty one or the style's own.

    No template means the style's default, so the file name keeps following the title style.
    """
    value = patch.get(FILENAME_TEMPLATE)
    if not isinstance(value, str):
        return dict(patch)
    style = patch.get("title_style", current.title_style)
    if not value.strip() or value == library.style_filename_template(style):
        return {**patch, FILENAME_TEMPLATE: None}
    return dict(patch)


def patch_problems(patch: Mapping[str, Any]) -> dict[str, Problem]:
    """Each setting in the patch that can't be saved, with why. The folder check isn't run here."""
    known = field_types(Settings)
    unusable = set(invalid_keys(patch))
    problems: dict[str, Problem] = {}
    for key, value in patch.items():
        problem = _setting_problem(key, value, known, unusable)
        if problem:
            problems[key] = problem
    return problems


def _setting_problem(
    key: str, value: Any, known: Mapping[str, Any], unusable: set[str]
) -> Problem | None:
    if key not in known:
        return {"reason": UNKNOWN_SETTING}
    # The store calls a relative folder unusable, but the folder's own check says why.
    if key == LIBRARY_DIR:
        return folder_path_problem(value) if isinstance(value, str) else {"reason": INVALID_VALUE}
    if key in unusable:
        return {"reason": INVALID_VALUE}
    check = VALUE_CHECKS.get(key)
    return check(value) if check else None


def template_problem(template: str, is_token: Callable[[str], bool]) -> Problem | None:
    """What's wrong with a template's tokens, or None when it has some and they're all known."""
    braced = BRACED.findall(template)
    for part in braced:
        if not is_token(part):
            return {"reason": UNKNOWN_TOKEN, "token": part}
    # A brace left over after the tokens is half of one, like "{title".
    if any(brace in BRACED.sub("", template) for brace in "{}"):
        return {"reason": OPEN_BRACE}
    if not braced:
        return {"reason": NO_TOKENS}
    return None


def _is_filename_token(part: str) -> bool:
    match = library.TOKEN_PATTERN.fullmatch(part)
    return match is not None and match["name"] in library.TOKEN_FIELDS


def _is_title_token(part: str) -> bool:
    return part[1:-1] in TITLE_TOKENS


def filename_template_problem(value: str | None) -> Problem | None:
    """A file name template's problem. None stands for the title style's default."""
    if value is None:
        return None
    return template_problem(value, _is_filename_token)


def title_template_problem(value: str) -> Problem | None:
    """A custom title or artist template's problem, with its own hint for {feat}."""
    if FEAT_WORD in value:
        return {"reason": FEAT_TOKEN}
    return template_problem(value, _is_title_token)


# Checks for values of the right type that can still be unusable. A new check is one line here.
VALUE_CHECKS: dict[str, Callable[[Any], Problem | None]] = {
    FILENAME_TEMPLATE: filename_template_problem,
    "custom_title_template": title_template_problem,
    "custom_artist_template": title_template_problem,
}


def probe_writing(folder: Path) -> None:
    """Create and delete a file in `folder`, since os.access can't tell on Windows."""
    handle, probe = paths.create_temp_file(folder, PROBE_PREFIX)
    os.close(handle)
    try:
        probe.unlink()
    except OSError as error:
        # The folder still counts as unusable, and the file stays rather than being tried again.
        log.warning(
            "The library folder check couldn't remove its own file: %s",
            error.strerror or type(error).__name__,
        )
        raise


def folder_path_problem(value: str) -> Problem | None:
    """What's wrong with a library folder's path, without touching the disk beyond looking."""
    if NULL in value:
        return {"reason": INVALID_VALUE}
    if not Path(value).is_absolute():
        return {"reason": NOT_ABSOLUTE}
    # os.path doesn't raise, where Path.exists() can on a folder that can't be read.
    if os.path.exists(value) and not os.path.isdir(value):
        return {"reason": NOT_A_FOLDER, "folder": value}
    return None


def library_dir_problem(value: str, probe: Probe | None = None) -> Problem | None:
    """Why songs can't be saved in this folder, or None. A missing folder is created."""
    problem = folder_path_problem(value)
    if problem:
        return problem
    folder = Path(value)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return _folder_failure(CANT_CREATE, folder, error)
    try:
        # Looked up here rather than as a default, so a test that replaces it isn't bypassed.
        (probe or probe_writing)(folder)
    except OSError as error:
        return _folder_failure(NOT_WRITABLE, folder, error)
    return None


def _folder_failure(reason: str, folder: Path, error: OSError) -> Problem:
    log.info(
        "The library folder can't be used (%s): %s", reason, error.strerror or type(error).__name__
    )
    log.debug("The library folder was %s", folder)
    return {"reason": reason, "folder": str(folder)}


def sample_source(sample: Any) -> SourceInfo | None:
    """The design's sample track with the caller's parts swapped in, or None if they're unusable."""
    if sample is None:
        sample = {}
    if not isinstance(sample, dict) or not set(sample) <= set(SAMPLE):
        return None
    values = {**SAMPLE, **sample}
    title, uploader, artists = values["title"], values["uploader"], values["artists"]
    if not isinstance(title, str) or not isinstance(uploader, str):
        return None
    if not isinstance(artists, list) or not all(isinstance(name, str) for name in artists):
        return None
    return SourceInfo(
        kind="soundcloud",
        id="sample",
        url=SAMPLE_URL,
        title=title,
        uploader=uploader,
        artists=artists,
        upload_date=SAMPLE_DATE,
        genre=SAMPLE_GENRE,
    )
