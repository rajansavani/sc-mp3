"""Download the yt-dlp zipapp, Deno and an older yt-dlp-ejs, each checked against its SHA-256.

The default versions are pinned with their full SHA-256. The script writes downloads/manifest.json,
which run_spike.py reads, so versions chosen here carry through to the runs.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SPIKE_DIR = Path(__file__).absolute().parent
DOWNLOADS_DIR = SPIKE_DIR / "downloads"
MANIFEST = DOWNLOADS_DIR / "manifest.json"
TIMEOUT_SECONDS = 120
USER_AGENT = "sc-mp3-spike"

# The nightly is the default because the latest stable, 2026.08.19, is the bundled version.
DEFAULT_YT_DLP_REPO = "yt-dlp/yt-dlp-nightly-builds"
DEFAULT_YT_DLP_TAG = "2026.10.10.233141"
DEFAULT_DENO_TAG = "v2.9.7"
DENO_ASSET = "deno-x86_64-pc-windows-msvc.zip"
# An EJS release older than the 0.8.0 the overlay expects, to stage a version mismatch.
DEFAULT_OLD_EJS_VERSION = "0.7.0"

# The default downloads, pinned so that a release asset replaced later is noticed.
PINNED_SHA256 = {
    "yt-dlp-2026.10.10.233141/yt-dlp": (
        "f40b6a9e4c5786360953911cab5d2f87507626b8fb7bfee3f8f1c2e5cf341fae"
    ),
    "deno-v2.9.7/deno-x86_64-pc-windows-msvc.zip": (
        "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238"
    ),
    "yt-dlp-ejs-0.7.0/yt_dlp_ejs-0.7.0-py3-none-any.whl": (
        "967e9cbe114ddfd046ff4668af18b1827b4597e2e47a83deea668a355828c798"
    ),
}

SHA256_PATTERN = re.compile(r"\b[0-9a-fA-F]{64}\b")


@dataclass
class Download:
    """One file to fetch, the folder it goes in, and how to find its URL and published SHA-256."""

    folder: str
    file_name: str
    locate: Callable[[], tuple[str, str]]

    @property
    def key(self) -> str:
        return f"{self.folder}/{self.file_name}"

    @property
    def path(self) -> Path:
        return DOWNLOADS_DIR / self.folder / self.file_name


def read_url(url: str) -> bytes:
    """Return the body of a URL."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_from_checksum_file(url: str, file_name: str) -> str:
    """Find a file's hash in a release's SHA2-256SUMS, or the only hash in a one-file checksum."""
    text = read_url(url).decode("utf-8-sig")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == file_name:
            return parts[0].lower()
    hashes = SHA256_PATTERN.findall(text)
    if len(hashes) != 1:
        raise ValueError(f"Couldn't find one SHA-256 for {file_name} in {url}.")
    return hashes[0].lower()


def locate_on_pypi(project: str, version: str, file_name: str) -> tuple[str, str]:
    """Return the URL and SHA-256 that PyPI's JSON API lists for one file of a release."""
    release = json.loads(read_url(f"https://pypi.org/pypi/{project}/{version}/json"))
    for file in release["urls"]:
        if file["filename"] == file_name:
            return file["url"], file["digests"]["sha256"]
    raise ValueError(f"PyPI lists no {file_name} for {project} {version}.")


def check_pin(download: Download, published: str) -> bool:
    """Raise if the published hash differs from the pinned one, and say whether there's a pin."""
    pinned = PINNED_SHA256.get(download.key)
    if pinned and pinned != published:
        raise ValueError(f"The published SHA-256 of {download.key} no longer matches the pin.")
    return pinned is not None


def is_in_place(download: Download) -> bool:
    """Check a file that's already there against its pin, or the hash saved at download time."""
    if not download.path.exists():
        return False
    want = PINNED_SHA256.get(download.key)
    saved = download.path.with_name(download.path.name + ".sha256")
    if want is None and saved.exists():
        want = saved.read_text(encoding="utf-8").strip()
    return want is not None and sha256_of(download.path.read_bytes()) == want


def fetch(download: Download) -> dict[str, Any]:
    """Make sure the file is in place with the right hash, downloading it when it isn't."""
    if is_in_place(download):
        print(f"Already there, SHA-256 checked: {download.path.relative_to(SPIKE_DIR)}")
        sha256 = sha256_of(download.path.read_bytes())
        return {"sha256": sha256, "pinned": download.key in PINNED_SHA256}
    # Work in a temporary folder and move it into place only when everything checked out.
    partial = DOWNLOADS_DIR / f".partial-{download.folder}"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    try:
        url, want = download.locate()
        pinned = check_pin(download, want)
        data = read_url(url)
        if sha256_of(data) != want:
            raise ValueError(f"SHA-256 mismatch for {download.key}.")
        (partial / download.file_name).write_bytes(data)
        (partial / f"{download.file_name}.sha256").write_text(want, encoding="utf-8")
        shutil.rmtree(download.path.parent, ignore_errors=True)
        partial.rename(download.path.parent)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    print(f"Verified {download.path.relative_to(SPIKE_DIR)} (SHA-256 {want})")
    return {"sha256": want, "pinned": pinned}


def unpack_deno(zip_path: Path) -> Path:
    """Unpack deno.exe next to the zip, unless an identical copy is already there."""
    with zipfile.ZipFile(zip_path) as archive:
        data = archive.read("deno.exe")
    exe = zip_path.with_name("deno.exe")
    if not exe.exists() or sha256_of(exe.read_bytes()) != sha256_of(data):
        exe.write_bytes(data)
        print(f"Unpacked {exe.relative_to(SPIKE_DIR)}")
    return exe


def yt_dlp_download(repo: str, tag: str) -> Download:
    """The official yt-dlp zipapp, the release asset named yt-dlp."""
    base = f"https://github.com/{repo}/releases/download/{tag}"
    return Download(
        f"yt-dlp-{tag}",
        "yt-dlp",
        lambda: (f"{base}/yt-dlp", sha256_from_checksum_file(f"{base}/SHA2-256SUMS", "yt-dlp")),
    )


def deno_download(tag: str) -> Download:
    """The Windows x64 Deno release zip."""
    url = f"https://github.com/denoland/deno/releases/download/{tag}/{DENO_ASSET}"
    return Download(
        f"deno-{tag}",
        DENO_ASSET,
        lambda: (url, sha256_from_checksum_file(f"{url}.sha256sum", DENO_ASSET)),
    )


def old_ejs_download(version: str) -> Download:
    """The yt-dlp-ejs wheel used to build the mismatched overlay."""
    file_name = f"yt_dlp_ejs-{version}-py3-none-any.whl"
    return Download(
        f"yt-dlp-ejs-{version}",
        file_name,
        lambda: locate_on_pypi("yt-dlp-ejs", version, file_name),
    )


def relative(path: Path) -> str:
    return path.relative_to(SPIKE_DIR).as_posix()


def main() -> int:
    """Download every component, write the manifest and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yt-dlp-repo", default=DEFAULT_YT_DLP_REPO)
    parser.add_argument("--yt-dlp-tag", default=DEFAULT_YT_DLP_TAG)
    parser.add_argument("--deno-tag", default=DEFAULT_DENO_TAG)
    parser.add_argument("--old-ejs-version", default=DEFAULT_OLD_EJS_VERSION)
    options = parser.parse_args()
    yt_dlp = yt_dlp_download(options.yt_dlp_repo, options.yt_dlp_tag)
    deno = deno_download(options.deno_tag)
    old_ejs = old_ejs_download(options.old_ejs_version)
    # A run that was killed outright can leave a temporary folder behind.
    for leftover in DOWNLOADS_DIR.glob(".partial-*"):
        shutil.rmtree(leftover)
    try:
        yt_dlp_result = fetch(yt_dlp)
        deno_result = fetch(deno)
        deno_exe = unpack_deno(deno.path)
        old_ejs_result = fetch(old_ejs)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1
    manifest = {
        "yt_dlp": {"tag": options.yt_dlp_tag, "path": relative(yt_dlp.path), **yt_dlp_result},
        # The SHA-256 is the zip's, since Deno publishes no hash for deno.exe itself.
        "deno": {
            "tag": options.deno_tag,
            "path": relative(deno_exe),
            "checked_file": relative(deno.path),
            **deno_result,
        },
        "old_ejs": {
            "version": options.old_ejs_version,
            "path": relative(old_ejs.path),
            **old_ejs_result,
        },
    }
    for name, entry in manifest.items():
        if not entry["pinned"]:
            print(f"Not pinned: {name} was checked only against its release's own checksum.")
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {relative(MANIFEST)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
