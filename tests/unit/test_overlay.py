"""Tests for loading yt-dlp from an engine update, at start-up and in the install's child process.

yt-dlp can be imported once per process, and this test session has imported it already, so
every load runs in a new Python process. Most updates are tiny fake yt-dlp packages in a zip.
"""

import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from scmp3.core import engine_start, overlay
from scmp3.core.engine_start import EngineStart
from scmp3.core.manifests import Manifest, read_manifest, write_manifest

NEWER = "2099.01.01"
OLDER = "2020.01.01"
# Seconds a child process may take: importing a real yt-dlp from a zip takes about a second.
CHILD_TIMEOUT = 120

# A yt-dlp that has just enough for the smoke test: YoutubeDL and one extractor.
FAKE_INIT = """
from yt_dlp.version import __version__

class YoutubeDL:
    def __init__(self, params=None):
        self.params = params or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_info_extractor(self, name):
        if name != "Soundcloud":
            raise KeyError(name)
        return object()
"""
BROKEN_INIT = "raise RuntimeError('a damaged update')\n"
NO_EXTRACTOR_INIT = FAKE_INIT.replace('"Soundcloud"', '"Other"')


def make_update(dest: Path, version: str = NEWER, init: str = FAKE_INIT) -> Path:
    """A zip with a fake yt-dlp package of this version, as the zipapp has it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as archive:
        archive.writestr("__main__.py", "")
        archive.writestr("yt_dlp/__init__.py", init)
        archive.writestr("yt_dlp/version.py", f"__version__ = {version!r}\n")
    return dest


def install(home: Path, version: str, *, init: str = FAKE_INIT, when: int = 1) -> Path:
    """An engine update in the components folder, as an install leaves it."""
    folder = home / "data" / "components" / "yt-dlp" / version
    zip_path = make_update(folder / "yt-dlp.zip", version, init)
    manifest = Manifest(
        name="yt-dlp",
        version=version,
        sha256="a" * 64,
        source_url=f"https://github.com/yt-dlp/yt-dlp/releases/download/{version}/yt-dlp",
        file="yt-dlp.zip",
        installed_at=datetime(2026, 10, when, tzinfo=UTC),
    )
    write_manifest(folder, manifest)
    return zip_path


def status(home: Path, version: str) -> str | None:
    manifest = read_manifest(home / "data" / "components" / "yt-dlp" / version)
    return manifest.status if manifest else None


# The start-up check runs first in the child, then reports which yt-dlp won.
START_SCRIPT = """
import dataclasses, json, os, sys
from scmp3.core import engine_start
started = engine_start.start_engine()
report = {"start": dataclasses.asdict(started), "plugins_off": "YTDLP_NO_PLUGINS" in os.environ}
if not started.restart:
    import yt_dlp
    report["file"] = yt_dlp.__file__
    report["version"] = yt_dlp.version.__version__
    # yt-dlp patches requests when it's imported, and a second patch breaks building any URL.
    import requests
    requests.Request("GET", "https://example.com/a b").prepare()
    report["requests_work"] = True
print(json.dumps(report))
"""


def run_python(
    script: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        check=False,
        env={**os.environ, **(env or {})},
    )


def start_in_child(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    result = run_python(START_SCRIPT, env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def check_in_child(zip_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scmp3", overlay.CHECK_FLAG, str(zip_path)],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        check=False,
    )


# At start-up


def test_without_an_update_the_built_in_copy_loads(scmp3_home: Path) -> None:
    report = start_in_child()
    assert report["start"] == {"update": None, "failed": None, "failure": None, "restart": False}
    assert report["version"] == yt_dlp.version.__version__
    assert report["plugins_off"] is True


def test_a_good_newer_update_is_imported_from_its_zip(scmp3_home: Path) -> None:
    zip_path = install(scmp3_home, NEWER)
    report = start_in_child()
    assert report["start"]["update"] == NEWER
    assert report["version"] == NEWER
    assert report["file"].startswith(str(zip_path) + os.sep)
    assert status(scmp3_home, NEWER) == "good"


def test_an_update_not_newer_than_the_built_in_copy_is_left_alone(scmp3_home: Path) -> None:
    install(scmp3_home, OLDER)
    report = start_in_child()
    assert report["start"]["update"] is None
    assert report["version"] == yt_dlp.version.__version__
    assert status(scmp3_home, OLDER) == "good"


@pytest.mark.parametrize("damage", ["deleted", "random bytes", "first half", "no yt_dlp"])
def test_a_damaged_update_falls_back_to_the_built_in_copy_and_is_marked_bad(
    scmp3_home: Path, damage: str
) -> None:
    zip_path = install(scmp3_home, NEWER)
    data = zip_path.read_bytes()
    if damage == "deleted":
        # A file that's gone isn't an update at all, so it isn't marked.
        zip_path.unlink()
    elif damage == "random bytes":
        zip_path.write_bytes(os.urandom(len(data)))
    elif damage == "first half":
        zip_path.write_bytes(data[: len(data) // 2])
    else:
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("__main__.py", "")
    report = start_in_child()
    assert report["start"]["update"] is None
    assert report["version"] == yt_dlp.version.__version__
    assert not report["file"].startswith(str(zip_path))
    # requests still works, since none of the update's code ran.
    assert report["requests_work"] is True
    if damage != "deleted":
        assert report["start"]["failed"] == NEWER
        assert report["start"]["failure"]
        assert status(scmp3_home, NEWER) == "bad"


def test_every_update_that_didnt_load_keeps_its_reason(scmp3_home: Path) -> None:
    install(scmp3_home, "2098.01.01", when=1)
    install(scmp3_home, "2098.06.01", when=2).write_bytes(b"not a zip")
    install(scmp3_home, NEWER, init=BROKEN_INIT, when=3)
    # A process before this one already found the newest broken after its code ran.
    earlier = f"{NEWER}: RuntimeError: a damaged update"
    report = start_in_child({engine_start.SKIP_ENV: NEWER, engine_start.FAILURE_ENV: earlier})
    assert report["start"]["update"] == "2098.01.01"
    assert report["start"]["failed"] == "2098.06.01"
    assert report["start"]["failure"] == (
        f"{earlier}; 2098.06.01: the update has no yt_dlp that Python can load"
    )


def test_a_damaged_update_gives_way_to_the_older_good_one(scmp3_home: Path) -> None:
    install(scmp3_home, "2098.01.01", when=1)
    newest = install(scmp3_home, NEWER, when=2)
    newest.write_bytes(b"not a zip")
    report = start_in_child()
    assert report["start"]["update"] == "2098.01.01"
    assert report["start"]["failed"] == NEWER
    assert status(scmp3_home, NEWER) == "bad"


@pytest.mark.parametrize("init", [BROKEN_INIT, NO_EXTRACTOR_INIT], ids=["import", "smoke test"])
def test_an_update_that_fails_once_its_code_runs_asks_for_a_restart(
    scmp3_home: Path, init: str
) -> None:
    install(scmp3_home, NEWER, init=init)
    report = start_in_child()
    assert report["start"]["restart"] is True
    assert report["start"]["failed"] == NEWER
    assert status(scmp3_home, NEWER) == "bad"


def test_the_restarted_process_skips_the_update_even_when_marking_it_failed(
    scmp3_home: Path,
) -> None:
    install(scmp3_home, NEWER)
    reason = "RuntimeError: a damaged update"
    report = start_in_child({engine_start.SKIP_ENV: NEWER, engine_start.FAILURE_ENV: reason})
    assert report["start"]["update"] is None
    assert report["start"]["failed"] == NEWER
    # The process before had no log yet, so this one logs why.
    assert report["start"]["failure"] == reason
    assert report["version"] == yt_dlp.version.__version__


def test_updates_marked_bad_or_reverted_are_never_tried(scmp3_home: Path) -> None:
    install(scmp3_home, NEWER)
    folder = scmp3_home / "data" / "components" / "yt-dlp" / NEWER
    manifest = read_manifest(folder)
    assert manifest is not None
    for marked in ("bad", "reverted"):
        write_manifest(folder, replace(manifest, status=marked))
        report = start_in_child()
        assert report["start"]["update"] is None
        assert report["version"] == yt_dlp.version.__version__


def test_with_yt_dlp_imported_already_nothing_is_tried(
    scmp3_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # So the plugin switch it sets is undone after the test.
    monkeypatch.delenv(overlay.NO_PLUGINS_ENV, raising=False)
    install(scmp3_home, NEWER)
    # This test process imported the built-in copy already.
    assert engine_start.start_engine() == EngineStart()
    assert status(scmp3_home, NEWER) == "good"


# Restarting


def test_a_restart_runs_the_app_again_skipping_the_failed_update() -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 3)

    started = EngineStart(failed=NEWER, failure="RuntimeError: broken", restart=True)
    environ = {"PATH": "x", engine_start.SKIP_ENV: "2098.01.01"}
    assert engine_start.restart_app(["get", "link"], started, environ=environ, run=run) == 3
    command, env = calls[0]
    assert command == [*overlay.own_command(), "get", "link"]
    assert env[engine_start.SKIP_ENV] == f"2098.01.01,{NEWER}"
    assert env[engine_start.FAILURE_ENV] == "RuntimeError: broken"
    assert env["PATH"] == "x"


def test_from_source_the_app_starts_again_through_python() -> None:
    assert overlay.own_command() == [sys.executable, "-m", "scmp3"]


def test_frozen_the_app_starts_again_through_its_own_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert overlay.own_command() == [sys.executable]


def test_a_failed_update_is_logged_once_logging_is_up(caplog: pytest.LogCaptureFixture) -> None:
    engine_start.log_start(EngineStart())
    assert caplog.text == ""
    failure = f"{NEWER}: RuntimeError: a damaged update"
    engine_start.log_start(EngineStart(failed=NEWER, failure=failure))
    assert "Download engine updates didn't load, so the built-in copy is used" in caplog.text
    assert failure in caplog.text


# The install's child process


def test_the_child_check_passes_a_good_update_and_prints_its_version(tmp_path: Path) -> None:
    result = check_in_child(make_update(tmp_path / "yt-dlp.zip"))
    assert result.returncode == overlay.EXIT_OK, result.stderr
    assert result.stdout.strip() == NEWER


@pytest.mark.parametrize(
    ("init", "says"),
    [(BROKEN_INIT, "RuntimeError: a damaged update"), (NO_EXTRACTOR_INIT, "KeyError")],
    ids=["import", "smoke test"],
)
def test_the_child_check_fails_an_update_whose_code_fails(
    tmp_path: Path, init: str, says: str
) -> None:
    result = check_in_child(make_update(tmp_path / "yt-dlp.zip", init=init))
    assert result.returncode == overlay.EXIT_FAILED
    assert says in result.stderr


def test_the_child_check_fails_a_file_that_isnt_an_update_without_importing_it(
    tmp_path: Path,
) -> None:
    garbage = tmp_path / "yt-dlp.zip"
    garbage.write_bytes(os.urandom(4096))
    result = check_in_child(garbage)
    assert result.returncode == overlay.EXIT_FAILED
    assert "the update has no yt_dlp that Python can load" in result.stderr


def test_the_child_check_needs_exactly_one_zip() -> None:
    assert overlay.check_here([]) == overlay.EXIT_USAGE
    assert overlay.check_here(["a", "b"]) == overlay.EXIT_USAGE


def test_failures_name_the_zip_without_its_folder(tmp_path: Path) -> None:
    zip_path = tmp_path / "deep" / "yt-dlp.zip"
    error = SyntaxError(f"invalid syntax ({zip_path}{os.sep}yt_dlp{os.sep}x.py, line 1)")
    described = overlay.describe(error, zip_path)
    assert str(tmp_path) not in described
    assert described.startswith("SyntaxError: invalid syntax (yt-dlp.zip")


# The real yt-dlp, zipped the way its release zipapp is


@pytest.fixture(scope="session")
def real_update(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The installed yt-dlp and yt-dlp-ejs in one zip, its version changed to a newer one."""
    import yt_dlp_ejs

    dest = tmp_path_factory.mktemp("real_update") / "yt-dlp.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", "import yt_dlp\nyt_dlp.main()\n")
        for package in (yt_dlp, yt_dlp_ejs):
            root = Path(package.__file__).parent
            for path in root.rglob("*"):
                if "__pycache__" in path.parts or path.is_dir():
                    continue
                name = path.relative_to(root.parent).as_posix()
                if name == "yt_dlp/version.py":
                    text = path.read_text(encoding="utf-8")
                    text = text.replace(f"'{yt_dlp.version.__version__}'", f"'{NEWER}'")
                    archive.writestr(name, text)
                else:
                    archive.write(path, name)
    return dest


def test_the_real_yt_dlp_passes_the_child_check_from_a_zip(real_update: Path) -> None:
    result = check_in_child(real_update)
    assert result.returncode == overlay.EXIT_OK, result.stderr
    assert result.stdout.strip() == NEWER


def test_the_real_yt_dlp_loads_from_an_update_with_requests_still_working(
    scmp3_home: Path, real_update: Path
) -> None:
    folder = scmp3_home / "data" / "components" / "yt-dlp" / NEWER
    folder.mkdir(parents=True)
    zip_path = folder / "yt-dlp.zip"
    zip_path.write_bytes(real_update.read_bytes())
    write_manifest(
        folder,
        Manifest(
            name="yt-dlp",
            version=NEWER,
            sha256="a" * 64,
            source_url="https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp",
            file="yt-dlp.zip",
            installed_at=datetime(2026, 10, 1, tzinfo=UTC),
        ),
    )
    report = start_in_child()
    assert report["start"]["update"] == NEWER
    assert report["file"].startswith(str(zip_path) + os.sep)
    assert report["requests_work"] is True
