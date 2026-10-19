"""Loads and saves settings.json.

The file has a schema version, and older versions are migrated as they load. Keys this version
doesn't know are written back unchanged. A file with unusable content is moved aside and the
defaults are used. A file that can't be opened is left alone, and saving is refused.
"""

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Settings, field_types

FILE_NAME = "settings.json"
VERSION_KEY = "version"
SCHEMA_VERSION = 1

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# Each entry upgrades a settings dict from the version it's keyed by to the next one.
MIGRATIONS: dict[int, Migration] = {}

# The JSON types each settings field type is stored as.
_JSON_TYPES: dict[Any, tuple[type, ...]] = {
    bool: (bool,),
    int: (int,),
    float: (int, float),
    str: (str,),
    Path: (str,),
    NoneType: (NoneType,),
}

log = logging.getLogger(__name__)


class InvalidSettings(ValueError):
    """A settings patch names unknown settings or has unusable values."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"unusable settings: {', '.join(keys)}")
        self.keys = keys


def default_path() -> Path:
    """Where settings.json lives."""
    return paths.config_dir() / FILE_NAME


class SettingsStore:
    """settings.json on disk. Safe to share between threads.

    Keys this version doesn't know are kept from the last `load` and written back by `save`.
    """

    def __init__(
        self,
        path: Path,
        *,
        version: int = SCHEMA_VERSION,
        migrations: Mapping[int, Migration] = MIGRATIONS,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = path
        self._version = version
        self._migrations = migrations
        self._clock = clock
        # Reentrant, because update() loads and saves while holding it.
        self._lock = threading.RLock()
        self._unknown: dict[str, Any] = {}
        # A newer version's file keeps its version number, so that version won't migrate it again.
        self._file_version = version
        # Set when the file couldn't be read or backed up, so a save can't destroy it.
        self._save_refused = False

    def load(self) -> Settings:
        """Read the settings, or return the defaults when the file is missing or unusable."""
        with self._lock:
            self._unknown = {}
            self._file_version = self._version
            self._save_refused = False
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                return Settings()
            except OSError as error:
                self._refuse_saves(error)
                return Settings()
            try:
                data, file_version = self._parse(raw)
            except (ValueError, TypeError, KeyError) as error:
                self._move_aside(error)
                return Settings()
            known = field_types(Settings)
            self._unknown = {key: value for key, value in data.items() if key not in known}
            self._file_version = max(file_version, self._version)
            return Settings.from_dict(_usable_values(data))

    def save(self, settings: Settings) -> None:
        """Write the settings atomically, along with the unknown keys from the last `load`."""
        with self._lock:
            if self._save_refused:
                raise ScmpError(
                    ErrorKind.disk, "settings.json couldn't be read, so it isn't overwritten"
                )
            data = {VERSION_KEY: self._file_version, **settings.to_dict(), **self._unknown}
            text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            paths.write_text_atomically(self.path, text)

    def update(self, patch: Mapping[str, Any]) -> Settings:
        """Apply setting names and JSON values to the saved settings, and return the result.

        Loading, changing and saving happen under one lock. Raises InvalidSettings, and saves
        nothing, when any name or value in the patch is unusable.
        """
        known = field_types(Settings)
        problems = [key for key in patch if key not in known] + invalid_keys(patch)
        if problems:
            raise InvalidSettings(problems)
        with self._lock:
            changed = Settings.from_dict({**self.load().to_dict(), **patch})
            self.save(changed)
            return changed

    def _parse(self, raw: bytes) -> tuple[dict[str, Any], int]:
        """Decode and migrate the file. Returns the data and the version the file had."""
        # utf-8-sig also accepts the byte order mark some Windows editors add.
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("settings.json doesn't hold a JSON object")
        # Version 1 is the first, so a file without a version is treated as one.
        version = data.pop(VERSION_KEY, 1)
        if type(version) is not int or version < 1:
            raise ValueError(f"settings.json has an unusable version: {version!r}")
        file_version = version
        while version < self._version:
            data = self._migrations[version](data)
            version += 1
        return data, file_version

    def _refuse_saves(self, error: OSError) -> None:
        """Use the defaults for now, without overwriting a file that may still be good."""
        self._save_refused = True
        log.warning(
            "settings.json couldn't be opened (%s), so the defaults are in use until a restart"
            " and changes won't be saved",
            error.strerror or error,
        )
        log.debug("Reading %s failed", self.path, exc_info=error)

    def _move_aside(self, error: Exception) -> None:
        """Rename the unusable file settings.bad-<timestamp>.json."""
        log.debug("Reading %s failed", self.path, exc_info=error)
        backup = paths.backup_path(self.path, self._clock())
        try:
            os.replace(self.path, backup)
        except OSError as move_error:
            # Without a backup, the file is the only copy, so it mustn't be overwritten either.
            self._refuse_saves(move_error)
            return
        log.warning(
            "settings.json couldn't be read (%s), so it was renamed %s and the defaults are in use",
            type(error).__name__,
            backup.name,
        )


def _is_absolute_folder(value: str) -> bool:
    """A full path, so saved songs never land in whatever the working folder is."""
    return bool(value) and Path(value).is_absolute()


# Checks beyond the type, for settings where a value of the right type can still be unusable.
_VALUE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "library_dir": _is_absolute_folder,
}


def invalid_keys(data: Mapping[str, Any]) -> list[str]:
    """The settings in `data` whose values have the wrong type, aren't a choice, or are unusable."""
    types = field_types(Settings)
    return [
        key
        for key, value in data.items()
        if key in types and not (_fits(types[key], value) and _passes_check(key, value))
    ]


def _passes_check(key: str, value: Any) -> bool:
    """Whether a value that has the right type also passes its setting's own check, if any."""
    check = _VALUE_CHECKS.get(key)
    return check is None or check(value)


def _usable_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """The known settings whose values are usable. The rest get their defaults, with a warning."""
    bad = invalid_keys(data)
    if bad:
        log.warning(
            "These settings had unusable values, so they use the defaults: %s", ", ".join(bad)
        )
    known = field_types(Settings)
    return {key: value for key, value in data.items() if key in known and key not in bad}


def _fits(hint: Any, value: Any) -> bool:
    """Whether a JSON value fits a settings field's type."""
    origin = get_origin(hint)
    if origin is Literal:
        # Compare types too, so True doesn't pass for 1.
        return any(type(value) is type(choice) and value == choice for choice in get_args(hint))
    if origin is UnionType or origin is Union:
        return any(_fits(option, value) for option in get_args(hint))
    if origin is list:
        (item_type,) = get_args(hint)
        return isinstance(value, list) and all(_fits(item_type, item) for item in value)
    return type(value) in _JSON_TYPES[hint]
