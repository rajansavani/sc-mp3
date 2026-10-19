"""Which release file a component installs: the latest one when it can be verified, else the pin.

Each component's repo, release files and pinned release are in components.toml, which ships
inside the package.
"""

import functools
import logging
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath

from scmp3.core import fetch, github
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.github import Clock, Release
from scmp3.core.net import Opener

SPEC_FILE = "components.toml"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    """A component's release file for one OS and CPU, with the pinned release's hash of it."""

    asset: str
    member: str  # the binary inside the zip
    pinned_sha256: str


@dataclass(frozen=True)
class ComponentSpec:
    """Where a component's releases come from, as components.toml describes it."""

    name: str
    repo: str
    checksums: str  # the release's checksum file, where {asset} stands for the asset's name
    pinned_tag: str
    targets: Mapping[str, Target]  # by <os>-<arch>, as paths.platform_tag() names them


@dataclass(frozen=True)
class Download:
    """One version of a component to download, and how to unpack it."""

    name: str
    version: str
    url: str
    sha256: str
    file_name: str  # the installed file's name
    member: str | None = None  # the file to take out of a zip; None installs the download itself
    executable: bool = False


@functools.cache
def load_spec(name: str) -> ComponentSpec:
    """A component's entry in components.toml, which ships inside the package."""
    source = resources.files(__package__).joinpath(SPEC_FILE)
    entry = tomllib.loads(source.read_text(encoding="utf-8"))[name]
    targets = {key: Target(**values) for key, values in entry["targets"].items()}
    return ComponentSpec(
        name=name,
        repo=entry["repo"],
        checksums=entry["checksums"],
        pinned_tag=entry["pinned_tag"],
        targets=targets,
    )


def choose_download(
    spec: ComponentSpec, platform_tag: str | None, *, opener: Opener, clock: Clock
) -> Download:
    """The latest release when it can be verified, otherwise the pinned release.

    `platform_tag` is the machine as <os>-<arch>, or None for an OS or CPU we don't know.
    """
    target = spec.targets.get(platform_tag or "")
    if target is None:
        where = platform_tag or "this OS and CPU"
        raise ScmpError(ErrorKind.unavailable, f"{spec.name} has no build for {where}")
    try:
        release = github.github_latest(spec.repo, opener=opener, clock=clock)
    except ScmpError as error:
        if error.kind != ErrorKind.rate_limited:
            raise
        log.info("GitHub is rate limited, so the pinned %s is installed", spec.name)
        return _pinned_download(spec, target)
    latest = _latest_download(spec, target, release, opener)
    if latest is None:
        return _pinned_download(spec, target)
    return latest


def _latest_download(
    spec: ComponentSpec, target: Target, release: Release, opener: Opener
) -> Download | None:
    """The release's file with its published SHA-256, or None when it publishes none."""
    asset = release.assets.get(target.asset)
    checksum_file = release.assets.get(spec.checksums.format(asset=target.asset))
    if asset is None or checksum_file is None:
        log.warning(
            "%s %s has no %s or no checksum file for it, so the pinned release is used",
            spec.name,
            release.version,
            target.asset,
        )
        return None
    sha256 = fetch.fetch_checksums(checksum_file.url, opener=opener).get(target.asset)
    if sha256 is None:
        log.warning(
            "%s doesn't list %s, so the pinned release is used", checksum_file.name, target.asset
        )
        return None
    return _download_of(spec, target, release.version, asset.url, sha256)


def _pinned_download(spec: ComponentSpec, target: Target) -> Download:
    """The pinned release's file, checked against the hash in components.toml."""
    url = github.download_url(spec.repo, spec.pinned_tag, target.asset)
    version = github.tag_version(spec.pinned_tag)
    return _download_of(spec, target, version, url, target.pinned_sha256)


def _download_of(
    spec: ComponentSpec, target: Target, version: str, url: str, sha256: str
) -> Download:
    return Download(
        name=spec.name,
        version=version,
        url=url,
        sha256=sha256.lower(),
        file_name=PurePosixPath(target.member).name,
        member=target.member,
        # Every component in components.toml is a program.
        executable=True,
    )
