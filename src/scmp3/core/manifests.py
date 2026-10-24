"""The manifest.json beside each installed version of a component: what it records, and its I/O.

It also holds the rule for the names that become path parts in the components folder.
"""

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Literal

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.fetch import os_reason

MANIFEST_FILE = "manifest.json"

# Names, versions and file names become path parts, so they're kept to these characters.
SAFE_PART = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]*")

# Good versions can be used. A bad one failed to load, and a reverted one was set aside in
# Settings. Neither is used again until it's installed again.
ComponentStatus = Literal["good", "bad", "reverted"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Manifest:
    """What manifest.json records about one installed version."""

    name: str
    version: str
    sha256: str  # the download's
    source_url: str
    file: str  # the installed file, in the version's folder
    installed_at: datetime
    status: ComponentStatus = "good"

    def to_dict(self) -> dict[str, str]:
        """The manifest as JSON-safe text fields."""
        return {**asdict(self), "installed_at": self.installed_at.isoformat()}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Manifest":
        """Rebuild from `to_dict` output, raising ValueError for a missing or mistyped field."""
        texts = {item.name: data.get(item.name) for item in fields(cls)}
        if not all(isinstance(value, str) for value in texts.values()):
            raise ValueError("a field is missing or isn't text")
        installed_at = datetime.fromisoformat(texts.pop("installed_at"))
        # Sorting by install time would fail on a mix of times with and without a zone.
        if installed_at.tzinfo is None:
            raise ValueError("installed_at has no time zone")
        return cls(**texts, installed_at=installed_at)


def safe_part(part: str) -> str:
    """A name or version checked to be one plain path part, never .. or a path."""
    if not SAFE_PART.fullmatch(part):
        raise ScmpError(ErrorKind.internal, f"{part!r} can't be a component folder name")
    return part


def read_manifest(version_dir: Path) -> Manifest | None:
    """A version's manifest, or None for a temp folder or an install that didn't finish."""
    if version_dir.name.startswith(".") or not version_dir.is_dir():
        return None
    where = f"{version_dir.parent.name}/{version_dir.name}"
    try:
        data = (version_dir / MANIFEST_FILE).read_bytes()
    except FileNotFoundError:
        # The install stopped before writing it, so the version doesn't count.
        return None
    except OSError as error:
        log.warning("Couldn't read the manifest of %s: %s", where, os_reason(error))
        log.debug("Reading the manifest of %s failed", where, exc_info=True)
        return None
    try:
        # json reads the bytes itself, so text that isn't UTF-8 is a ValueError here too.
        manifest = Manifest.from_dict(json.loads(data))
    except (ValueError, AttributeError) as error:
        log.warning("The manifest of %s is damaged, so it's ignored: %s", where, error)
        return None
    if manifest.version != version_dir.name or not SAFE_PART.fullmatch(manifest.file):
        log.warning("The manifest of %s names another version or file, so it's ignored", where)
        return None
    return manifest


def write_manifest(version_dir: Path, manifest: Manifest) -> None:
    """Write a version's manifest atomically, which is what makes the version count as installed."""
    text = json.dumps(manifest.to_dict(), indent=2) + "\n"
    paths.write_text_atomically(version_dir / MANIFEST_FILE, text)
