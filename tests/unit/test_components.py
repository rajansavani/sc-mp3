"""Tests for the components manager and the Deno install, with local zips and a fake HTTP layer."""

import hashlib
import io
import json
import logging
import os
import sys
import threading
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scmp3.core import components, fetch, paths
from scmp3.core.components import DENO, Components
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.manifests import ComponentStatus, Manifest
from scmp3.core.releases import ComponentSpec, Download, Target

NOW = 1_800_000_000.0
REPO = "example/tool"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download"
ASSET = "tool-x86_64-pc-windows-msvc.zip"
MEMBER = "tool.exe"


def make_zip(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


LATEST_ZIP = make_zip({MEMBER: b"tool 2.0.0", "README.md": b"not installed"})
PINNED_ZIP = make_zip({MEMBER: b"tool 1.0.0"})

SPEC = ComponentSpec(
    name="tool",
    repo=REPO,
    checksums="{asset}.sha256sum",
    pinned_tag="v1.0.0",
    targets={"windows-x64": Target(ASSET, MEMBER, sha256(PINNED_ZIP))},
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
        self._lock = threading.Lock()

    def serve(
        self, url: str, body: bytes, status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        self.files[url] = (status, body, dict(headers or {}))

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        with self._lock:
            self.requests.append(url)
        if url not in self.files:
            raise AssertionError(f"unexpected request for {url}")
        status, body, response_headers = self.files[url]
        return FakeResponse(body, status, {"Content-Length": str(len(body)), **response_headers})

    def asked_for(self, suffix: str) -> int:
        return sum(1 for url in self.requests if url.endswith(suffix))


def serve_release(
    web: FakeWeb,
    *,
    repo: str = REPO,
    tag: str = "v2.0.0",
    files: Mapping[str, bytes],
) -> None:
    """Serve a latest release with these files, as GitHub's API and download servers do."""
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    assets = []
    for name, data in files.items():
        url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
        web.serve(url, data)
        assets.append({"name": name, "browser_download_url": url})
    page = f"https://github.com/{repo}/releases/tag/{tag}"
    web.serve(api, json.dumps({"tag_name": tag, "html_url": page, "assets": assets}).encode())


def sha256sum_line(data: bytes, name: str) -> bytes:
    return f"{sha256(data)}  {name}\n".encode()


def get_filehash_listing(data: bytes, name: str) -> bytes:
    """A checksum file as Deno's Windows releases write it, with PowerShell's Get-FileHash."""
    return (
        f"\r\nAlgorithm : SHA256\r\nHash      : {sha256(data).upper()}\r\n"
        f"Path      : C:\\a\\deno\\deno\\target\\release\\{name}\r\n\r\n"
    ).encode()


class FakeRunner:
    """Stands in for runner.run, recording each command, and failing when told to."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[list[str], float, ErrorKind]] = []
        self.binaries: list[bytes] = []

    def __call__(self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind) -> None:
        self.calls.append((list(command), timeout, error_kind))
        self.binaries.append(Path(command[0]).read_bytes())
        if self.error is not None:
            raise self.error


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "components"


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def run() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(root: Path, web: FakeWeb, run: FakeRunner, clock: Clock) -> Components:
    return Components(root, opener=web, clock=clock, run=run, target="windows-x64")


def download(
    data: bytes = LATEST_ZIP, *, version: str = "2.0.0", member: str | None = MEMBER
) -> Download:
    return Download(
        name="tool",
        version=version,
        url=f"{DOWNLOADS}/v{version}/{ASSET}",
        sha256=sha256(data),
        file_name=MEMBER,
        member=member,
        executable=True,
    )


def every_file(folder: Path) -> list[str]:
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))


# A good manifest for tool 2.0.0, as manifest.json holds it.
MANIFEST_JSON = Manifest(
    name="tool",
    version="2.0.0",
    sha256="a" * 64,
    source_url=f"{DOWNLOADS}/v2.0.0/{ASSET}",
    file=MEMBER,
    installed_at=datetime.fromtimestamp(NOW, UTC),
).to_dict()


def write_manifest(root: Path, version: str, /, **changes: Any) -> Path:
    """An installed version made by hand: its file and a manifest, changed as given."""
    folder = root / "tool" / version
    folder.mkdir(parents=True)
    (folder / MEMBER).write_bytes(b"binary")
    data = {**MANIFEST_JSON, "version": version, **changes}
    (folder / "manifest.json").write_text(json.dumps(data))
    return folder


# Installing


def test_an_install_puts_the_file_and_a_manifest_in_the_versions_folder(
    store: Components, web: FakeWeb, root: Path
) -> None:
    web.serve(download().url, LATEST_ZIP)
    manifest = store.install(download())
    assert every_file(root) == [
        "tool",
        "tool/2.0.0",
        "tool/2.0.0/manifest.json",
        "tool/2.0.0/tool.exe",
    ]
    assert (root / "tool" / "2.0.0" / MEMBER).read_bytes() == b"tool 2.0.0"
    assert json.loads((root / "tool" / "2.0.0" / "manifest.json").read_text()) == {
        "name": "tool",
        "version": "2.0.0",
        "sha256": sha256(LATEST_ZIP),
        "source_url": download().url,
        "file": MEMBER,
        "installed_at": "2027-01-15T08:00:00+00:00",
        "status": "good",
    }
    assert manifest == store.active("tool")


def test_a_tampered_download_is_rejected_and_nothing_is_installed(
    store: Components, web: FakeWeb, root: Path
) -> None:
    tampered = make_zip({MEMBER: b"something else"})
    web.serve(download().url, tampered)
    with pytest.raises(ScmpError) as raised:
        store.install(download(LATEST_ZIP))
    assert raised.value.kind is ErrorKind.bad_download
    assert every_file(root) == []
    assert store.installed("tool") == []
    assert store.active_path("tool") is None


def spy_on(monkeypatch: pytest.MonkeyPatch, name: str) -> list[tuple[Any, ...]]:
    """Record each call to one of fetch's functions, which still does its work."""
    calls: list[tuple[Any, ...]] = []
    real = getattr(fetch, name)

    def recording(*args: Any) -> Any:
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(fetch, name, recording)
    return calls


DENO_ASSET = "deno-x86_64-pc-windows-msvc.zip"


def test_a_tampered_deno_is_never_unpacked_or_run(
    root: Path, web: FakeWeb, run: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = make_zip({"deno.exe": b"deno binary"})
    tampered = make_zip({"deno.exe": b"something else"})
    files = {DENO_ASSET: tampered, f"{DENO_ASSET}.sha256sum": sha256sum_line(published, DENO_ASSET)}
    serve_release(web, repo="denoland/deno", tag="v2.9.8", files=files)
    extracted = spy_on(monkeypatch, "extract_member")
    made_executable = spy_on(monkeypatch, "make_executable")
    store = Components(root, opener=web, run=run, target="windows-x64")
    with pytest.raises(ScmpError) as raised:
        store.install_deno()
    assert raised.value.kind is ErrorKind.bad_download
    assert (extracted, made_executable, run.calls) == ([], [], [])


def test_a_verified_deno_reaches_the_unpack_step_the_tampered_one_skips(
    root: Path, web: FakeWeb, run: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same spy sees a call once the hash matches, so the test above can't pass by accident.
    no_binary = make_zip({"other.exe": b"x"})
    files = {
        DENO_ASSET: no_binary,
        f"{DENO_ASSET}.sha256sum": sha256sum_line(no_binary, DENO_ASSET),
    }
    serve_release(web, repo="denoland/deno", tag="v2.9.8", files=files)
    extracted = spy_on(monkeypatch, "extract_member")
    store = Components(root, opener=web, run=run, target="windows-x64")
    with pytest.raises(ScmpError) as raised:
        store.install_deno()
    assert raised.value.kind is ErrorKind.bad_download
    assert len(extracted) == 1
    assert run.calls == []


def test_installing_is_true_only_while_an_install_runs(store: Components, web: FakeWeb) -> None:
    web.serve(download().url, LATEST_ZIP)
    during: list[bool] = []
    store.install(download(), check=lambda path: during.append(store.installing("tool")))
    assert during == [True]
    assert store.installing("tool") is False


def test_installing_is_false_again_after_a_failed_install(store: Components, web: FakeWeb) -> None:
    web.serve(download().url, b"tampered")
    with pytest.raises(ScmpError):
        store.install(download())
    assert store.installing("tool") is False


def test_installing_again_changes_nothing(store: Components, web: FakeWeb, root: Path) -> None:
    web.serve(download().url, LATEST_ZIP)
    first = store.install(download())
    binary = root / "tool" / "2.0.0" / MEMBER
    before = (every_file(root), binary.stat().st_mtime_ns)
    web.requests.clear()
    assert store.install(download()) == first
    assert web.requests == []
    assert (every_file(root), binary.stat().st_mtime_ns) == before


def test_the_check_sees_the_unpacked_file_before_it_moves_in(
    store: Components, web: FakeWeb, root: Path
) -> None:
    web.serve(download().url, LATEST_ZIP)
    seen: list[tuple[Path, bytes]] = []
    store.install(download(), check=lambda path: seen.append((path, path.read_bytes())))
    ((checked, content),) = seen
    assert content == b"tool 2.0.0"
    assert checked.parent.parent.name.startswith(components.WORK_PREFIX)
    assert not checked.exists()


def test_a_file_that_fails_its_check_isnt_installed(
    store: Components, web: FakeWeb, root: Path
) -> None:
    def check(path: Path) -> None:
        raise ScmpError(ErrorKind.bad_download, "doesn't run")

    web.serve(download().url, LATEST_ZIP)
    with pytest.raises(ScmpError) as raised:
        store.install(download(), check=check)
    assert raised.value.kind is ErrorKind.bad_download
    assert every_file(root) == []


def test_a_zip_without_the_binary_installs_nothing(
    store: Components, web: FakeWeb, root: Path
) -> None:
    other = make_zip({"other.exe": b"x"})
    web.serve(download().url, other)
    with pytest.raises(ScmpError) as raised:
        store.install(download(other))
    assert raised.value.kind is ErrorKind.bad_download
    assert every_file(root) == []


def test_a_failed_install_keeps_the_versions_already_there(
    store: Components, web: FakeWeb, root: Path
) -> None:
    write_manifest(root, "1.0.0")
    web.serve(download().url, b"tampered")
    with pytest.raises(ScmpError):
        store.install(download())
    assert [manifest.version for manifest in store.installed("tool")] == ["1.0.0"]


def test_an_install_that_stopped_before_its_manifest_is_replaced(
    store: Components, web: FakeWeb, root: Path
) -> None:
    unfinished = root / "tool" / "2.0.0"
    unfinished.mkdir(parents=True)
    (unfinished / MEMBER).write_bytes(b"half")
    web.serve(download().url, LATEST_ZIP)
    store.install(download())
    assert (unfinished / MEMBER).read_bytes() == b"tool 2.0.0"
    assert every_file(root / "tool") == ["2.0.0", "2.0.0/manifest.json", "2.0.0/tool.exe"]


@pytest.mark.parametrize(
    "changes",
    [{"status": "bad"}, {"sha256": "b" * 64}, {"file": "old.exe"}],
    ids=["marked bad", "another download", "another file"],
)
def test_an_installed_version_that_isnt_this_download_is_replaced(
    store: Components, web: FakeWeb, root: Path, changes: dict[str, str]
) -> None:
    write_manifest(root, "2.0.0", **changes)
    web.serve(download().url, LATEST_ZIP)
    manifest = store.install(download())
    assert manifest.status == "good"
    assert store.active("tool") == manifest


def test_leftovers_of_an_install_that_was_cut_off_are_removed(
    store: Components, web: FakeWeb, root: Path
) -> None:
    leftover = root / "tool" / f"{components.WORK_PREFIX}abc123"
    leftover.mkdir(parents=True)
    (leftover / "download").write_bytes(b"x" * 1000)
    web.serve(download().url, LATEST_ZIP)
    store.install(download())
    assert not leftover.exists()


def test_cleanup_warnings_give_the_reason_but_not_the_path(
    store: Components,
    web: FakeWeb,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def rmtree_that_fails(path: Path, onexc: Any) -> None:
        locked = str(Path(path) / "deno.exe")
        onexc(os.unlink, locked, PermissionError(13, "Access is denied", locked))

    web.serve(download().url, b"tampered")
    monkeypatch.setattr(components.shutil, "rmtree", rmtree_that_fails)
    with caplog.at_level(logging.DEBUG, logger="scmp3.core.components"), pytest.raises(ScmpError):
        store.install(download())
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert warnings
    assert all("Access is denied" in text for text in warnings)
    assert not any(str(root) in text for text in warnings)


def test_a_download_that_isnt_zipped_is_installed_as_it_is(
    store: Components, web: FakeWeb, root: Path
) -> None:
    zipapp = b"#!/usr/bin/env python3\nPK..."
    web.serve(download(zipapp, member=None).url, zipapp)
    store.install(download(zipapp, member=None))
    assert (root / "tool" / "2.0.0" / MEMBER).read_bytes() == zipapp


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute bits")
def test_an_installed_program_can_be_run(store: Components, web: FakeWeb, root: Path) -> None:
    web.serve(download().url, LATEST_ZIP)
    store.install(download())
    assert os.access(root / "tool" / "2.0.0" / MEMBER, os.X_OK)


@pytest.mark.parametrize("version", ["..", "../x", "a/b", "", ".hidden"])
def test_a_version_that_isnt_a_plain_folder_name_is_refused(
    store: Components, web: FakeWeb, version: str
) -> None:
    with pytest.raises(ScmpError) as raised:
        store.install(download(version=version))
    assert raised.value.kind is ErrorKind.internal
    assert web.requests == []


@pytest.mark.parametrize("file_name", ["C:\\elsewhere\\tool.exe", "../tool.exe", "/tmp/tool"])
def test_an_installed_file_name_that_isnt_a_plain_name_is_refused(
    store: Components, web: FakeWeb, file_name: str
) -> None:
    with pytest.raises(ScmpError) as raised:
        store.install(replace(download(), file_name=file_name))
    assert raised.value.kind is ErrorKind.internal
    assert web.requests == []


def test_a_components_folder_that_cant_be_written_is_a_disk_error(
    tmp_path: Path, web: FakeWeb
) -> None:
    blocked = tmp_path / "components"
    blocked.write_text("a file where the folder should be")
    store = Components(blocked, opener=web, target="windows-x64")
    with pytest.raises(ScmpError) as raised:
        store.install(download())
    assert raised.value.kind is ErrorKind.disk


# Listing and removing


def test_installed_lists_versions_oldest_install_first(store: Components, root: Path) -> None:
    write_manifest(root, "2.0.0", installed_at="2027-03-01T00:00:00+00:00")
    write_manifest(root, "10.0.0", installed_at="2027-02-01T00:00:00+00:00")
    assert [manifest.version for manifest in store.installed("tool")] == ["10.0.0", "2.0.0"]


def test_the_active_version_is_the_newest_good_install(store: Components, root: Path) -> None:
    write_manifest(root, "1.0.0", installed_at="2027-01-01T00:00:00+00:00")
    write_manifest(root, "2.0.0", installed_at="2027-02-01T00:00:00+00:00")
    write_manifest(root, "3.0.0", installed_at="2027-03-01T00:00:00+00:00", status="bad")
    active = store.active("tool")
    assert active is not None
    assert active.version == "2.0.0"
    assert store.active_path("tool") == root / "tool" / "2.0.0" / MEMBER


def test_a_version_whose_file_is_gone_isnt_active(store: Components, root: Path) -> None:
    folder = write_manifest(root, "2.0.0")
    (folder / MEMBER).unlink()
    assert store.active("tool") is None


def test_nothing_installed_means_nothing_active(store: Components) -> None:
    assert store.installed("tool") == []
    assert store.active("tool") is None
    assert store.active_path("tool") is None


def test_a_damaged_manifest_isnt_listed_or_active(store: Components, root: Path) -> None:
    # test_manifests.py covers the kinds of damage; this is how the listing treats one.
    folder = write_manifest(root, "2.0.0")
    (folder / "manifest.json").write_bytes(b'{"name": "tool", "version": "2.0.0\xff"}')
    assert store.installed("tool") == []
    assert store.active("tool") is None


@pytest.mark.parametrize("status", ["bad", "reverted"])
def test_a_version_marked_bad_or_reverted_stops_being_active(
    store: Components, root: Path, status: ComponentStatus
) -> None:
    write_manifest(root, "1.0.0", installed_at="2027-01-01T00:00:00+00:00")
    write_manifest(root, "2.0.0", installed_at="2027-02-01T00:00:00+00:00")
    newest = store.installed("tool")[-1]
    marked = store.set_status(newest, status)
    assert marked == replace(newest, status=status)
    assert store.installed("tool")[-1] == marked
    active = store.active("tool")
    assert active is not None
    assert active.version == "1.0.0"


def test_a_version_marked_good_again_is_active_again(store: Components, root: Path) -> None:
    write_manifest(root, "2.0.0", status="reverted")
    assert store.active("tool") is None
    store.set_status(store.installed("tool")[0], "good")
    active = store.active("tool")
    assert active is not None
    assert active.version == "2.0.0"


def test_remove_deletes_one_version(store: Components, root: Path) -> None:
    write_manifest(root, "1.0.0")
    write_manifest(root, "2.0.0")
    store.remove("tool", "1.0.0")
    assert [manifest.version for manifest in store.installed("tool")] == ["2.0.0"]


def test_remove_without_a_version_deletes_the_component(store: Components, root: Path) -> None:
    write_manifest(root, "1.0.0")
    write_manifest(root, "2.0.0")
    store.remove("tool")
    assert not (root / "tool").exists()


def test_removing_what_isnt_installed_is_fine(store: Components) -> None:
    store.remove("tool")
    store.remove("tool", "1.0.0")


def test_remove_refuses_a_path(store: Components) -> None:
    with pytest.raises(ScmpError) as raised:
        store.remove("tool", "..")
    assert raised.value.kind is ErrorKind.internal


# Installing the latest release or the pinned one. test_releases.py covers the choice itself.


def test_ensure_installs_the_latest_release_checked_against_its_checksum_file(
    store: Components, web: FakeWeb
) -> None:
    sums = sha256sum_line(LATEST_ZIP, ASSET)
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    manifest = store.ensure(SPEC)
    assert (manifest.version, manifest.sha256) == ("2.0.0", sha256(LATEST_ZIP))
    assert manifest.source_url == f"{DOWNLOADS}/v2.0.0/{ASSET}"


def test_ensure_with_a_version_installed_asks_nothing(store: Components, web: FakeWeb) -> None:
    sums = sha256sum_line(LATEST_ZIP, ASSET)
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    first = store.ensure(SPEC)
    web.requests.clear()
    assert store.ensure(SPEC) == first
    assert web.requests == []


def test_a_release_without_a_checksum_file_installs_the_pinned_release(
    store: Components, web: FakeWeb
) -> None:
    serve_release(web, files={ASSET: LATEST_ZIP})
    web.serve(f"{DOWNLOADS}/v1.0.0/{ASSET}", PINNED_ZIP)
    manifest = store.ensure(SPEC)
    assert (manifest.version, manifest.sha256) == ("1.0.0", sha256(PINNED_ZIP))
    assert web.asked_for(f"/v2.0.0/{ASSET}") == 0


def test_a_download_that_fails_its_published_checksum_isnt_swapped_for_the_pin(
    store: Components, web: FakeWeb, root: Path
) -> None:
    sums = sha256sum_line(PINNED_ZIP, ASSET)
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    with pytest.raises(ScmpError) as raised:
        store.ensure(SPEC)
    assert raised.value.kind is ErrorKind.bad_download
    assert web.asked_for(f"/v1.0.0/{ASSET}") == 0
    assert every_file(root) == []


def test_github_out_of_reach_is_offline_and_installs_nothing(root: Path, run: FakeRunner) -> None:
    def no_connection(url: str, headers: Mapping[str, str]) -> Any:
        raise OSError("network is unreachable")

    store = Components(root, opener=no_connection, run=run, target="windows-x64")
    with pytest.raises(ScmpError) as raised:
        store.ensure(SPEC)
    assert raised.value.kind is ErrorKind.offline
    assert store.installed("tool") == []


def test_two_installs_at_once_download_once(store: Components, web: FakeWeb) -> None:
    sums = sha256sum_line(LATEST_ZIP, ASSET)
    serve_release(web, files={ASSET: LATEST_ZIP, f"{ASSET}.sha256sum": sums})
    results: list[Manifest] = []
    threads = [threading.Thread(target=lambda: results.append(store.ensure(SPEC))) for _ in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == 2
    assert results[0] == results[1]
    assert web.asked_for(f"/{ASSET}") == 1


# Deno


@pytest.mark.parametrize(
    ("target", "asset", "member", "listing"),
    [
        ("windows-x64", "deno-x86_64-pc-windows-msvc.zip", "deno.exe", get_filehash_listing),
        ("linux-x64", "deno-x86_64-unknown-linux-gnu.zip", "deno", sha256sum_line),
        ("macos-arm64", "deno-aarch64-apple-darwin.zip", "deno", sha256sum_line),
    ],
)
def test_deno_installs_from_its_latest_release_and_must_run(
    root: Path,
    web: FakeWeb,
    run: FakeRunner,
    target: str,
    asset: str,
    member: str,
    listing: Any,
) -> None:
    deno_zip = make_zip({member: b"deno binary"})
    files = {asset: deno_zip, f"{asset}.sha256sum": listing(deno_zip, asset)}
    serve_release(web, repo="denoland/deno", tag="v2.9.8", files=files)
    store = Components(root, opener=web, run=run, target=target)
    manifest = store.install_deno()
    deno = root / "deno" / "2.9.8" / member
    assert (manifest.version, manifest.file) == ("2.9.8", member)
    assert store.active_path(DENO) == deno
    # It ran from the temp folder, before it was moved into place.
    ((command, timeout, kind),) = run.calls
    assert command[1:] == ["--version"]
    assert Path(command[0]).name == member
    assert run.binaries == [b"deno binary"]
    assert (timeout, kind) == (components.CHECK_TIMEOUT, ErrorKind.bad_download)


def test_a_deno_that_doesnt_run_isnt_installed(root: Path, web: FakeWeb) -> None:
    asset = "deno-x86_64-pc-windows-msvc.zip"
    deno_zip = make_zip({"deno.exe": b"not really deno"})
    files = {asset: deno_zip, f"{asset}.sha256sum": sha256sum_line(deno_zip, asset)}
    serve_release(web, repo="denoland/deno", tag="v2.9.8", files=files)
    run = FakeRunner(ScmpError(ErrorKind.bad_download, "can't start deno"))
    store = Components(root, opener=web, run=run, target="windows-x64")
    with pytest.raises(ScmpError) as raised:
        store.install_deno()
    assert raised.value.kind is ErrorKind.bad_download
    assert every_file(root) == []


def test_check_runs_accepts_a_program_that_runs(root: Path) -> None:
    Components(root).check_runs(Path(sys.executable))


def test_check_runs_refuses_a_file_that_cant_run(root: Path, tmp_path: Path) -> None:
    not_a_program = tmp_path / "deno.exe"
    not_a_program.write_text("<html>not a program</html>")
    with pytest.raises(ScmpError) as raised:
        Components(root).check_runs(not_a_program)
    assert raised.value.kind is ErrorKind.bad_download


def test_the_default_folder_is_the_app_components_folder(scmp3_home: Path) -> None:
    assert Components().root == paths.components_dir()
    assert Components().root.is_relative_to(scmp3_home)


def test_a_versions_folder_that_cant_be_read_lists_nothing_and_says_why(
    store: Components, root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    write_manifest(root, "2.0.0")
    listing = Path.iterdir

    def refuse(self: Path) -> Any:
        if self == root / "tool":
            raise PermissionError(13, "Access is denied", str(self))
        return listing(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert store.installed("tool") == []
    assert store.active("tool") is None
    assert "Couldn't list the installed tool versions: Access is denied" in caplog.text
    assert str(root) not in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete an open file")
def test_a_version_whose_file_is_held_keeps_its_manifest_until_it_can_go(
    store: Components, root: Path
) -> None:
    folder = write_manifest(root, "1.0.0")
    with (folder / MEMBER).open("rb"), pytest.raises(ScmpError) as raised:
        store.remove("tool", "1.0.0")
    assert raised.value.kind is ErrorKind.disk
    # Still listed, so the next removal tries again.
    assert [manifest.version for manifest in store.installed("tool")] == ["1.0.0"]
    store.remove("tool", "1.0.0")
    assert not folder.exists()
