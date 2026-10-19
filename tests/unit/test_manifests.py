"""Tests for the manifest.json beside each installed version: reading, writing and damage."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.manifests import Manifest, read_manifest, safe_part, write_manifest

MANIFEST = Manifest(
    name="tool",
    version="2.0.0",
    sha256="a" * 64,
    source_url="https://github.com/example/tool/releases/download/v2.0.0/tool.zip",
    file="tool.exe",
    installed_at=datetime.fromtimestamp(1_800_000_000, UTC),
)


def version_folder(tmp_path: Path, text: str | bytes | None = None, **changes: Any) -> Path:
    """A version's folder holding `text` as its manifest, or MANIFEST changed as given."""
    folder = tmp_path / "tool" / "2.0.0"
    folder.mkdir(parents=True)
    if text is None:
        text = json.dumps({**MANIFEST.to_dict(), **changes})
    data = text.encode() if isinstance(text, str) else text
    (folder / "manifest.json").write_bytes(data)
    return folder


def test_a_written_manifest_reads_back_the_same(tmp_path: Path) -> None:
    folder = tmp_path / "tool" / "2.0.0"
    folder.mkdir(parents=True)
    write_manifest(folder, MANIFEST)
    assert read_manifest(folder) == MANIFEST
    assert json.loads((folder / "manifest.json").read_text())["status"] == "good"


def test_a_version_without_a_manifest_doesnt_count(tmp_path: Path) -> None:
    folder = tmp_path / "tool" / "2.0.0"
    folder.mkdir(parents=True)
    assert read_manifest(folder) is None


def test_a_temp_folder_is_never_read_as_a_version(tmp_path: Path) -> None:
    folder = tmp_path / "tool" / ".install-abc"
    folder.mkdir(parents=True)
    write_manifest(folder, MANIFEST)
    assert read_manifest(folder) is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"name": "tool", "version": "2.0.0"}),
        json.dumps({**MANIFEST.to_dict(), "version": 2}),
    ],
    ids=["not json", "a list", "missing fields", "a number"],
)
def test_a_damaged_manifest_is_ignored(tmp_path: Path, text: str) -> None:
    assert read_manifest(version_folder(tmp_path, text)) is None


def test_a_manifest_that_isnt_utf8_is_ignored(tmp_path: Path) -> None:
    folder = version_folder(tmp_path, b'{"name": "tool", "version": "2.0.0\xff"}')
    assert read_manifest(folder) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"installed_at": "2027-01-01T00:00:00"},
        {"installed_at": "yesterday"},
        {"version": "3.0.0"},
        {"file": "../../evil.exe"},
    ],
    ids=["a time without a zone", "not a time", "another version", "a path"],
)
def test_a_manifest_that_doesnt_fit_its_folder_is_ignored(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    assert read_manifest(version_folder(tmp_path, **changes)) is None


@pytest.mark.parametrize("part", ["deno", "2.9.7", "2026.10.18.232758", "deno.exe", "1.0+build"])
def test_plain_names_are_safe_path_parts(part: str) -> None:
    assert safe_part(part) == part


@pytest.mark.parametrize("part", ["..", "../x", "a/b", "a\\b", "C:\\x", "", ".hidden"])
def test_a_name_that_could_be_a_path_is_refused(part: str) -> None:
    with pytest.raises(ScmpError) as raised:
        safe_part(part)
    assert raised.value.kind is ErrorKind.internal
