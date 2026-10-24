"""Tests for engine updates: which one the next start uses, installing, refusing and reverting."""

import hashlib
import importlib.metadata
import io
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

import pytest

from scmp3.core import engine_updates
from scmp3.core.components import Components
from scmp3.core.engine_updates import EngineUpdate, EngineUpdates, built_in_version
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.manifests import ComponentStatus, Manifest, read_manifest, write_manifest
from scmp3.core.overlay import CHECK_FLAG

NOW = 1_800_000_000.0
BUILT_IN = "2026.8.19"
NIGHTLY = "yt-dlp/yt-dlp-nightly-builds"
LATEST = "2026.10.10.233141"
ZIPAPP = b"PK the nightly zipapp"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()


class FakeGitHub:
    """Serves one latest yt-dlp release per repo, and records every URL asked for."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []

    def release(self, tag: str, repo: str = NIGHTLY, zipapp: bytes = ZIPAPP) -> None:
        downloads = f"https://github.com/{repo}/releases/download/{tag}"
        assets = [
            {"name": "yt-dlp", "browser_download_url": f"{downloads}/yt-dlp"},
            {"name": "SHA2-256SUMS", "browser_download_url": f"{downloads}/SHA2-256SUMS"},
        ]
        page = f"https://github.com/{repo}/releases/tag/{tag}"
        body = {"tag_name": tag, "html_url": page, "assets": assets}
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        self.files[api] = json.dumps(body).encode()
        self.files[f"{downloads}/yt-dlp"] = zipapp
        self.files[f"{downloads}/SHA2-256SUMS"] = f"{sha256(zipapp)}  yt-dlp\n".encode()

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.files:
            raise ConnectionRefusedError(f"nothing serves {url}")
        return FakeResponse(self.files[url])

    def downloads(self) -> list[str]:
        return [url for url in self.requests if url.endswith("/yt-dlp")]


class FakeRunner:
    """Stands in for the child process that checks an update, passing or failing it."""

    def __init__(self, error: ScmpError | None = None) -> None:
        self.error = error
        self.commands: list[list[str]] = []
        self.checked: list[bytes] = []

    def __call__(self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind) -> None:
        self.commands.append(list(command))
        self.checked.append(Path(command[-1]).read_bytes())
        if self.error is not None:
            raise self.error


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "components"


@pytest.fixture
def web() -> FakeGitHub:
    fake = FakeGitHub()
    fake.release(LATEST)
    return fake


def updates_for(
    root: Path,
    web: FakeGitHub,
    run: FakeRunner | None = None,
    *,
    in_use: str | None = None,
    built_in: str | None = BUILT_IN,
) -> EngineUpdates:
    return EngineUpdates(
        Components(root, opener=web, clock=lambda: NOW),
        built_in=built_in,
        in_use=in_use,
        opener=web,
        clock=lambda: NOW,
        run=run or FakeRunner(),
        command=lambda: ["sc-mp3.exe"],
    )


def install_by_hand(
    root: Path, version: str, *, status: ComponentStatus = "good", day: int = 1
) -> Path:
    """An update as an earlier install left it, with its zip and manifest."""
    folder = root / "yt-dlp" / version
    folder.mkdir(parents=True)
    (folder / "yt-dlp.zip").write_bytes(b"PK " + version.encode())
    manifest = Manifest(
        name="yt-dlp",
        version=version,
        sha256="a" * 64,
        source_url=f"https://github.com/{NIGHTLY}/releases/download/{version}/yt-dlp",
        file="yt-dlp.zip",
        installed_at=datetime(2026, 10, day, tzinfo=UTC),
        status=status,
    )
    write_manifest(folder, manifest)
    return folder


def status_of(root: Path, version: str) -> str | None:
    manifest = read_manifest(root / "yt-dlp" / version)
    return manifest.status if manifest else None


def versions(root: Path) -> list[str]:
    folder = root / "yt-dlp"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


# The built-in copy


def test_the_built_in_version_comes_from_package_metadata() -> None:
    assert built_in_version() == importlib.metadata.version("yt-dlp")


def test_missing_metadata_leaves_the_built_in_version_unknown_and_says_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(engine_updates.importlib.metadata, "version", no_metadata)
    with caplog.at_level(logging.WARNING, logger=engine_updates.__name__):
        assert built_in_version() is None
    assert "yt-dlp's package metadata is missing" in caplog.text


# Which update the next start uses


def test_the_next_start_uses_the_newest_good_update(root: Path, web: FakeGitHub) -> None:
    install_by_hand(root, "2026.09.01", day=1)
    install_by_hand(root, "2026.10.01", day=2)
    install_by_hand(root, "2026.10.05", status="bad", day=3)
    upcoming = updates_for(root, web).next_update()
    assert upcoming is not None
    assert upcoming.version == "2026.10.01"


def test_an_update_no_newer_than_the_built_in_copy_isnt_used(root: Path, web: FakeGitHub) -> None:
    install_by_hand(root, "2026.08.19")
    assert updates_for(root, web).next_update() is None


def test_an_update_whose_zip_is_gone_isnt_used(root: Path, web: FakeGitHub) -> None:
    folder = install_by_hand(root, LATEST)
    (folder / "yt-dlp.zip").unlink()
    assert updates_for(root, web).next_update() is None


def test_skipped_versions_arent_used(root: Path, web: FakeGitHub) -> None:
    install_by_hand(root, "2026.09.01", day=1)
    install_by_hand(root, LATEST, day=2)
    upcoming = updates_for(root, web).next_update(skip={LATEST})
    assert upcoming is not None
    assert upcoming.version == "2026.09.01"


def test_with_the_built_in_version_unknown_any_update_is_used(root: Path, web: FakeGitHub) -> None:
    install_by_hand(root, "2020.01.01")
    assert updates_for(root, web, built_in=None).next_update() is not None


def test_the_failed_update_is_the_newest_one_when_its_bad(root: Path, web: FakeGitHub) -> None:
    install_by_hand(root, "2026.09.01", day=1)
    updates = updates_for(root, web)
    assert updates.failed_update() is None
    install_by_hand(root, LATEST, status="bad", day=2)
    failed = updates.failed_update()
    assert failed is not None
    assert failed.version == LATEST


# Installing


def test_a_newer_release_is_checked_in_a_child_process_then_installed(
    root: Path, web: FakeGitHub
) -> None:
    run = FakeRunner()
    updates = updates_for(root, web, run)
    assert updates.update("nightly") == EngineUpdate(LATEST, installed=True)
    # The child got the downloaded zip before it moved into place.
    assert run.checked == [ZIPAPP]
    assert run.commands[0][:2] == ["sc-mp3.exe", CHECK_FLAG]
    assert ".install-" in run.commands[0][2]
    upcoming = updates.next_update()
    assert upcoming is not None
    assert upcoming.version == LATEST
    assert (root / "yt-dlp" / LATEST / "yt-dlp.zip").read_bytes() == ZIPAPP


def test_a_release_that_isnt_newer_installs_nothing(root: Path, web: FakeGitHub) -> None:
    web.release("2026.08.19")
    run = FakeRunner()
    assert updates_for(root, web, run).update("nightly") == EngineUpdate("2026.08.19", False)
    assert web.downloads() == []
    assert run.commands == []
    assert versions(root) == []


def test_the_installed_update_counts_as_what_the_next_start_uses(
    root: Path, web: FakeGitHub
) -> None:
    updates = updates_for(root, web)
    updates.update("nightly")
    web.requests.clear()
    assert updates.update("nightly") == EngineUpdate(LATEST, installed=False)
    assert web.downloads() == []


def test_an_update_that_fails_its_check_isnt_installed(root: Path, web: FakeGitHub) -> None:
    run = FakeRunner(ScmpError(ErrorKind.bad_download, "RuntimeError: a damaged update"))
    updates = updates_for(root, web, run)
    with pytest.raises(ScmpError) as raised:
        updates.update("nightly")
    assert raised.value.kind is ErrorKind.bad_download
    assert updates.next_update() is None
    assert versions(root) == []


def test_a_tampered_download_is_never_checked_or_installed(root: Path, web: FakeGitHub) -> None:
    downloads = f"https://github.com/{NIGHTLY}/releases/download/{LATEST}"
    web.files[f"{downloads}/yt-dlp"] = b"tampered"
    run = FakeRunner()
    with pytest.raises(ScmpError) as raised:
        updates_for(root, web, run).update("nightly")
    assert raised.value.kind is ErrorKind.bad_download
    assert run.commands == []


def test_the_channel_picks_the_repo(root: Path, web: FakeGitHub) -> None:
    web.release("2026.09.30", repo="yt-dlp/yt-dlp")
    assert updates_for(root, web).update("stable").version == "2026.09.30"


@pytest.mark.parametrize("status", ["bad", "reverted"])
def test_a_version_set_aside_before_isnt_installed_again_by_the_daily_check(
    root: Path, web: FakeGitHub, status: ComponentStatus
) -> None:
    install_by_hand(root, LATEST, status=status)
    found = updates_for(root, web).update("nightly")
    assert found == EngineUpdate(LATEST, installed=False, refused=status)
    assert web.downloads() == []
    assert status_of(root, LATEST) == status


@pytest.mark.parametrize("status", ["bad", "reverted"])
def test_update_now_installs_a_version_set_aside_before(
    root: Path, web: FakeGitHub, status: ComponentStatus
) -> None:
    install_by_hand(root, LATEST, status=status)
    assert updates_for(root, web).update("nightly", again=True).installed is True
    assert status_of(root, LATEST) == "good"
    assert (root / "yt-dlp" / LATEST / "yt-dlp.zip").read_bytes() == ZIPAPP


def test_update_now_on_the_reverted_update_in_use_marks_it_good_without_a_download(
    root: Path, web: FakeGitHub
) -> None:
    install_by_hand(root, LATEST, status="reverted")
    updates = updates_for(root, web, in_use=LATEST)
    assert updates.update("nightly", again=True) == EngineUpdate(LATEST, installed=True)
    assert web.downloads() == []
    assert status_of(root, LATEST) == "good"


def test_an_install_removes_the_updates_nothing_runs_or_will_run(
    root: Path, web: FakeGitHub
) -> None:
    install_by_hand(root, "2026.08.20", day=1)
    install_by_hand(root, "2026.09.01", day=2)
    install_by_hand(root, "2026.09.15", status="bad", day=3)
    install_by_hand(root, "2026.10.01", day=4)
    updates_for(root, web, in_use="2026.09.01").update("nightly")
    # This process runs 2026.09.01, and another one may have started on 2026.10.01.
    assert versions(root) == ["2026.09.01", "2026.10.01", LATEST]


def test_an_old_update_that_cant_be_removed_waits_for_a_later_install(
    root: Path, web: FakeGitHub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install_by_hand(root, "2026.09.01", day=1)
    install_by_hand(root, "2026.10.01", day=2)

    def refuse(self: Components, name: str, version: str | None = None) -> None:
        raise ScmpError(ErrorKind.disk, "can't remove 2026.09.01: Access is denied")

    monkeypatch.setattr(Components, "remove", refuse)
    assert updates_for(root, web).update("nightly").installed is True
    assert "An old engine update wasn't removed" in caplog.text
    assert versions(root) == ["2026.09.01", "2026.10.01", LATEST]


def test_github_out_of_reach_is_offline_and_changes_nothing(root: Path) -> None:
    with pytest.raises(ScmpError) as raised:
        updates_for(root, FakeGitHub()).update("nightly")
    assert raised.value.kind is ErrorKind.offline
    assert versions(root) == []


# Reverting


def test_revert_sets_every_update_aside_so_the_built_in_copy_runs_next(
    root: Path, web: FakeGitHub
) -> None:
    install_by_hand(root, "2026.09.01", day=1)
    install_by_hand(root, LATEST, status="bad", day=2)
    updates = updates_for(root, web)
    updates.revert()
    assert updates.next_update() is None
    assert updates.failed_update() is None
    assert [status_of(root, version) for version in versions(root)] == ["reverted", "reverted"]
    # The files stay, since this process may be running one of them.
    assert (root / "yt-dlp" / "2026.09.01" / "yt-dlp.zip").is_file()


def test_reverting_with_nothing_installed_is_fine(root: Path, web: FakeGitHub) -> None:
    updates_for(root, web).revert()
    assert versions(root) == []
