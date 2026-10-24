"""GitHub releases: asking for a repo's latest release, and where a release's files are served.

Requests aren't signed in, so GitHub allows about 60 an hour. Answers are cached with their ETag,
and nothing is asked while the rate limit is used up.
"""

import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.net import Opener, Response, network_errors, open_url, read_capped, status_error

API_URL = "https://api.github.com"
DOWNLOAD_URL = "https://github.com"
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
# A release's JSON is tens of kilobytes, even with dozens of assets.
MAX_RELEASE_BYTES = 5_000_000
# Enough of an error's body to find GitHub's message in it.
MAX_ERROR_BYTES = 64 * 1024

# owner/name, as in denoland/deno.
REPO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
# A tag becomes a folder name, so it's held to the characters versions use.
VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]*")
# GitHub's SHA-256 of an asset, which it has published since 2025 next to each file.
DIGEST_PATTERN = re.compile(r"sha256:(?P<hash>[0-9a-f]{64})")

# The wait when a rate limit doesn't say when it ends, as GitHub's docs advise.
DEFAULT_LIMIT_WAIT = 60.0
# The longest wait, since GitHub's limit resets hourly and our clock may be off from its clock.
MAX_LIMIT_WAIT = 3600.0

log = logging.getLogger(__name__)

# Seconds since the epoch, as time.time gives them; GitHub's rate limit resets are in those.
type Clock = Callable[[], float]


def tag_version(tag: str) -> str:
    """The version a release tag names, without a leading v: 2.9.7 for v2.9.7."""
    return tag.removeprefix("v")


def download_url(repo: str, tag: str, asset: str) -> str:
    """Where GitHub serves one file of a release, without going through the API."""
    return f"{DOWNLOAD_URL}/{repo}/releases/download/{tag}/{asset}"


@dataclass(frozen=True)
class Asset:
    """One file attached to a release, with GitHub's SHA-256 of it when the release has one."""

    name: str
    url: str
    sha256: str | None = None


@dataclass(frozen=True)
class Release:
    """A GitHub release: its tag, its page with the release notes, and its files by name."""

    repo: str
    tag: str
    assets: Mapping[str, Asset] = field(default_factory=dict)
    html_url: str = ""

    @property
    def version(self) -> str:
        return tag_version(self.tag)

    def to_github_json(self) -> dict[str, Any]:
        """The release in the shape of GitHub's own JSON, which is how the cache keeps it."""
        assets = [_asset_json(asset) for asset in self.assets.values()]
        return {"tag_name": self.tag, "html_url": self.html_url, "assets": assets}


def _asset_json(asset: Asset) -> dict[str, str]:
    data = {"name": asset.name, "browser_download_url": asset.url}
    if asset.sha256 is not None:
        data["digest"] = f"sha256:{asset.sha256}"
    return data


@dataclass(frozen=True)
class _CachedRelease:
    """What's remembered about one repo's latest release between calls."""

    release: Release | None = None
    etag: str | None = None
    # Seconds since the epoch before which GitHub's API isn't asked again.
    limited_until: float | None = None


def github_latest(
    repo: str,
    *,
    opener: Opener = open_url,
    clock: Clock = time.time,
    cache_dir: Path | None = None,
) -> Release:
    """The latest release of a GitHub repo, such as denoland/deno.

    While the rate limit is used up, this returns the cached release without asking, and raises
    ScmpError(rate_limited) when there's none.
    """
    cache_path = _cache_path(repo, cache_dir)
    cached = _read_cache(repo, cache_path)
    if _still_limited(cached.limited_until, clock()):
        return _cached_or_rate_limited(cached, repo)

    url = f"{API_URL}/repos/{repo}/releases/latest"
    headers = dict(API_HEADERS)
    if cached.release is not None and cached.etag:
        # An unchanged release then comes back as a bodiless 304.
        headers["If-None-Match"] = cached.etag
    with network_errors(url):
        response = opener(url, headers)
    with response:
        limited_until = _limit_end(response, clock())
        if response.status == 304 and cached.release is not None:
            release, etag = cached.release, cached.etag
        elif response.status == 200:
            release = _parse_release(repo, read_capped(response, url, MAX_RELEASE_BYTES))
            etag = response.headers.get("ETag")
        elif limited_until is not None:
            _write_cache(cache_path, replace(cached, limited_until=limited_until))
            return _cached_or_rate_limited(cached, repo)
        else:
            raise status_error(response.status, url)
    _write_cache(cache_path, _CachedRelease(release, etag, limited_until))
    return release


def cached_release(repo: str, *, cache_dir: Path | None = None) -> Release | None:
    """The release github_latest last saw for a repo, without asking GitHub, or None."""
    return _read_cache(repo, _cache_path(repo, cache_dir)).release


def _cache_path(repo: str, cache_dir: Path | None) -> Path:
    """Where answers for a repo are cached, after checking that it's a plain owner/name."""
    if not REPO_PATTERN.fullmatch(repo):
        raise ScmpError(ErrorKind.internal, f"{repo!r} isn't a GitHub repo name like owner/name")
    return (cache_dir or paths.cache_dir() / "github") / f"{repo}.json"


def _still_limited(limited_until: float | None, now: float) -> bool:
    """Whether a remembered rate limit still holds.

    No wait is ever saved more than MAX_LIMIT_WAIT ahead, so one that far ahead means the clock
    was fast when it was saved, or the cache is damaged. Either way it's ignored.
    """
    if limited_until is None or not math.isfinite(limited_until):
        return False
    return now < limited_until <= now + MAX_LIMIT_WAIT


def _limit_end(response: Response, now: float) -> float | None:
    """When the API may be asked again, or None when the rate limit isn't used up."""
    headers = response.headers
    limited = response.status in (403, 429)
    retry_after = _as_number(headers.get("Retry-After"))
    if limited and retry_after is not None:
        wait = retry_after
    elif headers.get("X-RateLimit-Remaining") == "0":
        reset = _as_number(headers.get("X-RateLimit-Reset"))
        wait = DEFAULT_LIMIT_WAIT if reset is None else reset - now
    elif response.status == 429 or (response.status == 403 and _says_rate_limited(response)):
        # GitHub's secondary limits can come without headers, and then the wait is a minute.
        wait = DEFAULT_LIMIT_WAIT
    else:
        return None
    # A reset before our clock's now means the two clocks differ; GitHub may be asked again.
    return now + min(max(wait, 0.0), MAX_LIMIT_WAIT)


def _says_rate_limited(response: Response) -> bool:
    """Whether an error's body is GitHub's rate limit message, as in "secondary rate limit"."""
    with network_errors(API_URL):
        body = response.read(MAX_ERROR_BYTES)
    return b"rate limit" in body.lower()


def _as_number(text: str | None) -> float | None:
    """A header's value as a finite number, or None when it's missing or isn't one."""
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _cached_or_rate_limited(cached: _CachedRelease, repo: str) -> Release:
    """The cached release while the rate limit lasts, since asking again would fail."""
    if cached.release is not None:
        log.info("GitHub's rate limit is used up, so the cached release of %s is used", repo)
        return cached.release
    detail = f"GitHub's rate limit is used up, so {repo}'s latest release isn't known"
    raise ScmpError(ErrorKind.rate_limited, detail)


def _parse_release(repo: str, body: bytes) -> Release:
    """The release in GitHub's answer."""
    try:
        data = json.loads(body)
    except ValueError as error:
        raise _unreadable_release(repo) from error
    return _release_from(repo, data)


def _release_from(repo: str, data: Any) -> Release:
    """The tag, the release notes' page and the files from GitHub's JSON for one release."""
    try:
        tag = data["tag_name"]
        html_url = data["html_url"]
        assets = [_asset_from(item) for item in data["assets"]]
    except (LookupError, TypeError, AttributeError) as error:
        raise _unreadable_release(repo) from error
    texts = [tag, html_url, *(asset.name for asset in assets), *(asset.url for asset in assets)]
    if not all(isinstance(text, str) for text in texts):
        raise _unreadable_release(repo)
    if not VERSION_PATTERN.fullmatch(tag_version(tag)):
        raise _unreadable_release(repo)
    # The UI opens this page in the browser, so it must be a web page.
    if not html_url.startswith("https://"):
        raise _unreadable_release(repo)
    assets_by_name = {asset.name: asset for asset in assets}
    return Release(repo=repo, tag=tag, assets=assets_by_name, html_url=html_url)


def _asset_from(item: Any) -> Asset:
    """One asset from GitHub's JSON. A digest that isn't a SHA-256 is left out."""
    digest = item.get("digest")
    found = DIGEST_PATTERN.fullmatch(digest) if isinstance(digest, str) else None
    sha256 = found["hash"] if found else None
    return Asset(name=item["name"], url=item["browser_download_url"], sha256=sha256)


def _unreadable_release(repo: str) -> ScmpError:
    return ScmpError(ErrorKind.service_down, f"GitHub's answer for {repo}'s release is unreadable")


def _read_cache(repo: str, path: Path) -> _CachedRelease:
    """The cached answer for a repo, or an empty one when there's none or it's damaged."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return _CachedRelease()
    except OSError as error:
        reason = error.strerror or type(error).__name__
        log.warning("Couldn't read the cached GitHub release %s: %s", path.name, reason)
        log.debug("Reading the cached GitHub release failed", exc_info=True)
        return _CachedRelease()
    try:
        # json reads the bytes itself, so text that isn't UTF-8 is a ValueError here too.
        return _cache_from(repo, json.loads(data))
    except (ValueError, AttributeError, ScmpError) as error:
        log.warning(
            "The cached GitHub release %s is damaged, so it's ignored: %s", path.name, error
        )
        return _CachedRelease()


def _cache_from(repo: str, data: Any) -> _CachedRelease:
    """Rebuild a cache entry from its JSON, raising ValueError when a field has the wrong type."""
    etag = data.get("etag")
    limited_until = data.get("limited_until")
    if not isinstance(etag, str | None) or not isinstance(limited_until, int | float | None):
        raise ValueError("a field has the wrong type")
    release_data = data.get("release")
    release = None if release_data is None else _release_from(repo, release_data)
    return _CachedRelease(release, etag, limited_until)


def _write_cache(path: Path, cached: _CachedRelease) -> None:
    """Save the answer for next time. Failing only costs a request later, so it's logged."""
    release = cached.release.to_github_json() if cached.release else None
    data = {"etag": cached.etag, "limited_until": cached.limited_until, "release": release}
    try:
        paths.write_text_atomically(path, json.dumps(data))
    except ScmpError as error:
        log.warning("Couldn't cache GitHub's answer: %s", error)
