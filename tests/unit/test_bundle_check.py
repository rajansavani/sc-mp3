"""Tests for packaging/bundle_check.py, which finds the builder's paths in a built app folder."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "bundle_check.py"


def load_script() -> ModuleType:
    """Import the module by its path, under a name that can't hide another module."""
    spec = importlib.util.spec_from_file_location("scmp3_packaging_bundle_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = load_script()


@pytest.fixture(autouse=True)
def no_packed_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pretend exes hold no PyInstaller archive."""
    monkeypatch.setattr(check, "packed_entries", lambda exe: iter(()))


def make_app(folder: Path, files: dict[str, bytes] | None = None) -> Path:
    """A pretend app folder with sc-mp3.exe and the given files."""
    app = folder / "sc-mp3"
    contents = {"sc-mp3.exe": b"MZ", "_internal/python313.dll": b"dll", **(files or {})}
    for name, data in contents.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return app


def leaks(app: Path, private_paths: list[str]) -> list[str]:
    return check.leaks(app, app / "sc-mp3.exe", private_paths)


def test_a_clean_app_folder_has_no_leaks(tmp_path: Path) -> None:
    assert leaks(make_app(tmp_path), ["C:\\Users\\builder", "/Users/builder"]) == []


@pytest.mark.parametrize("name", ["direct_url.json", "uv_cache.json", "DELVEWHEEL"])
def test_local_only_metadata_is_a_leak(tmp_path: Path, name: str) -> None:
    app = make_app(tmp_path, {f"_internal/sc_mp3-0.0.1.dist-info/{name}": b"{}"})
    assert [Path(found).name for found in leaks(app, [])] == [name]


def test_a_record_line_for_a_local_only_file_is_a_leak(tmp_path: Path) -> None:
    record = "\n".join(
        [
            "sc_mp3-0.0.1.dist-info/METADATA,sha256=abc,1346",
            "sc_mp3-0.0.1.dist-info/direct_url.json,sha256=def,105",
            "sc_mp3.pth,sha256=ghi,62",
        ]
    )
    app = make_app(tmp_path, {"_internal/sc_mp3-0.0.1.dist-info/RECORD": record.encode()})
    found = " ".join(leaks(app, []))
    assert "line for sc_mp3-0.0.1.dist-info/direct_url.json" in found
    assert "line for sc_mp3.pth" in found
    assert "METADATA" not in found


def test_a_record_without_local_only_files_is_clean(tmp_path: Path) -> None:
    record = b"sc_mp3-0.0.1.dist-info/METADATA,sha256=abc,1346\n"
    assert leaks(make_app(tmp_path, {"_internal/x.dist-info/RECORD": record}), []) == []


HOME = "C:\\Users\\Дмитрий"


@pytest.mark.parametrize(
    ("private", "content"),
    [
        ("C:\\Users\\builder", b'{"url": "file:///C:/Users/builder/sc-mp3"}'),
        ("C:\\Users\\builder", b"C:\\USERS\\BUILDER\\sc-mp3\\src"),
        ("C:\\Users\\builder", "c:\\users\\builder".encode("utf-16-le")),
        ("C:\\Users\\builder", b"x" + "C:\\Users\\builder".encode("utf-16-le")),
        ("C:\\Users\\builder", b'{"path": "C:\\\\Users\\\\builder\\\\sc-mp3"}'),
        (HOME, f"{HOME}\\sc-mp3".encode()),
        (HOME, HOME.upper().encode()),
        ("C:\\Users\\John Smith", b'{"url": "file:///C:/Users/John%20Smith/sc-mp3"}'),
        (HOME, b"file:///C:/Users/%D0%94%D0%BC%D0%B8%D1%82%D1%80%D0%B8%D0%B9/sc-mp3"),
        ("/Users/builder", b"/Users/builder/sc-mp3/src"),
        ("/Users/John Smith", b"file:///Users/John%20Smith/sc-mp3"),
    ],
    ids=[
        "forward slashes",
        "other case",
        "utf-16",
        "utf-16 at an odd byte",
        "json backslashes",
        "non-ascii as is",
        "non-ascii other case",
        "url-encoded space",
        "url-encoded non-ascii",
        "mac home",
        "mac home url-encoded",
    ],
)
def test_every_spelling_of_a_private_path_is_a_leak(
    tmp_path: Path, private: str, content: bytes
) -> None:
    app = make_app(tmp_path, {"_internal/data/info.txt": b"before " + content + b" after"})
    assert [Path(found).name for found in leaks(app, [private])] == ["info.txt"]


def test_a_private_path_packed_inside_the_exe_is_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The PYZ holds the compiled modules, and its strings only show once it's decompressed.
    packed = [("sc-mp3.exe!PYZ.pyz!scmp3._probe", b"\xfa\x16C:\\Users\\builder\\sc-mp3")]
    monkeypatch.setattr(check, "packed_entries", lambda exe: iter(packed))
    assert leaks(make_app(tmp_path), ["C:\\Users\\builder"]) == ["sc-mp3.exe!PYZ.pyz!scmp3._probe"]
