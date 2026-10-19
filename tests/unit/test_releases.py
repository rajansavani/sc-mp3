"""Tests for choosing a component's release file, and for the pins in components.toml."""

import hashlib
import io
import json
import re
from collections.abc import Mapping
from email.message import Message

import pytest

from scmp3.core import paths, releases
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.releases import ComponentSpec, Download, Target, load_spec

NOW = 1_800_000_000.0
REPO = "example/tool"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download"
ASSET = "tool-x86_64-pc-windows-msvc.zip"
MEMBER = "tool.exe"
LATEST_ZIP = b"tool 2.0.0 zip"
PINNED_ZIP = b"tool 1.0.0 zip"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SPEC = ComponentSpec(
    name="tool",
    repo=REPO,
    checksums="{asset}.sha256sum",
    pinned_tag="v1.0.0",
    targets={"windows-x64": Target(ASSET, MEMBER, sha256(PINNED_ZIP))},
)

PINNED = Download(
    name="tool",
    version="1.0.0",
    url=f"{DOWNLOADS}/v1.0.0/{ASSET}",
    sha256=sha256(PINNED_ZIP),
    file_name=MEMBER,
    member=MEMBER,
    executable=True,
)


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(
        self, body: bytes = b"", status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value


class FakeWeb:
    """Serves canned files by URL, fresh for every request, and records every URL asked for."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[int, bytes, dict[str, str]]] = {}
        self.requests: list[str] = []

    def serve(
        self, url: str, body: bytes, status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        self.files[url] = (status, body, dict(headers or {}))

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.files:
            raise AssertionError(f"unexpected request for {url}")
        status, body, response_headers = self.files[url]
        return FakeResponse(body, status, response_headers)


def serve_release(
    web: FakeWeb, *, repo: str = REPO, tag: str = "v2.0.0", files: Mapping[str, bytes]
) -> None:
    """Serve a latest release with these files, as GitHub's API and download servers do."""
    assets = []
    for name, data in files.items():
        url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
        web.serve(url, data)
        assets.append({"name": name, "browser_download_url": url})
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    web.serve(api, json.dumps({"tag_name": tag, "assets": assets}).encode())


def sha256sum_line(data: bytes, name: str) -> bytes:
    return f"{sha256(data)}  {name}\n".encode()


def choose(web: FakeWeb, spec: ComponentSpec = SPEC, platform_tag: str = "windows-x64") -> Download:
    return releases.choose_download(spec, platform_tag, opener=web, clock=lambda: NOW)


# Choosing the download


def test_the_latest_release_is_chosen_with_its_published_hash() -> None:
    web = FakeWeb()
    sums = sha256sum_line(LATEST_ZIP, ASSET)
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    assert choose(web) == Download(
        name="tool",
        version="2.0.0",
        url=f"{DOWNLOADS}/v2.0.0/{ASSET}",
        sha256=sha256(LATEST_ZIP),
        file_name=MEMBER,
        member=MEMBER,
        executable=True,
    )
    # Only the release and its checksum file are fetched; the download comes later.
    assert web.requests == [API, f"{DOWNLOADS}/v2.0.0/{ASSET}.sha256sum"]


def test_a_release_without_a_checksum_file_gives_the_pinned_release() -> None:
    web = FakeWeb()
    serve_release(web, files={ASSET: LATEST_ZIP})
    assert choose(web) == PINNED


def test_a_release_without_this_machines_file_gives_the_pinned_release() -> None:
    web = FakeWeb()
    serve_release(web, files={"tool-other.zip": LATEST_ZIP})
    assert choose(web) == PINNED


def test_a_checksum_file_that_doesnt_list_the_file_gives_the_pinned_release() -> None:
    web = FakeWeb()
    sums = sha256sum_line(LATEST_ZIP, "tool-other.zip")
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    assert choose(web) == PINNED


def test_a_rate_limited_api_with_nothing_cached_gives_the_pinned_release() -> None:
    web = FakeWeb()
    web.serve(API, b"{}", 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"})
    assert choose(web) == PINNED


def test_github_out_of_reach_is_offline() -> None:
    def no_connection(url: str, headers: Mapping[str, str]) -> FakeResponse:
        raise OSError("network is unreachable")

    with pytest.raises(ScmpError) as raised:
        releases.choose_download(SPEC, "windows-x64", opener=no_connection, clock=lambda: NOW)
    assert raised.value.kind is ErrorKind.offline


@pytest.mark.parametrize("platform_tag", ["linux-riscv64", None])
def test_a_machine_without_a_build_is_unavailable_without_asking(platform_tag: str | None) -> None:
    web = FakeWeb()
    with pytest.raises(ScmpError) as raised:
        releases.choose_download(SPEC, platform_tag, opener=web, clock=lambda: NOW)
    assert raised.value.kind is ErrorKind.unavailable
    assert web.requests == []


def test_deno_without_a_checksum_file_gives_the_shipped_pin() -> None:
    web = FakeWeb()
    asset = "deno-x86_64-pc-windows-msvc.zip"
    serve_release(web, repo="denoland/deno", tag="v2.9.8", files={asset: b"unverifiable"})
    spec = load_spec("deno")
    download = choose(web, spec)
    assert download.url == f"https://github.com/denoland/deno/releases/download/v2.9.7/{asset}"
    assert download.sha256 == spec.targets["windows-x64"].pinned_sha256
    assert (download.version, download.file_name) == ("2.9.7", "deno.exe")


# components.toml


def test_deno_is_pinned_for_every_os_and_cpu_the_app_knows() -> None:
    spec = load_spec("deno")
    known = {
        f"{os_name}-{arch}"
        for os_name in paths.OS_NAMES.values()
        for arch in set(paths.ARCH_NAMES.values())
    }
    assert set(spec.targets) == known
    assert spec.repo == "denoland/deno"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", spec.pinned_tag)
    assert "{asset}" in spec.checksums


@pytest.mark.parametrize("target", sorted(load_spec("deno").targets))
def test_each_deno_pin_names_its_zip_its_binary_and_a_sha256(target: str) -> None:
    pin = load_spec("deno").targets[target]
    assert re.fullmatch(r"deno-[a-z0-9_]+-[a-z0-9_-]+\.zip", pin.asset)
    assert pin.member == ("deno.exe" if target.startswith("windows") else "deno")
    assert re.fullmatch(r"[0-9a-f]{64}", pin.pinned_sha256)


def test_deno_pins_are_all_different() -> None:
    targets = load_spec("deno").targets.values()
    assert len({pin.asset for pin in targets}) == len({pin.pinned_sha256 for pin in targets}) == 6
