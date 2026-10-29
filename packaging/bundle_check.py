"""Find what in a built app would publish where it was built: local-only metadata, or paths.

packaging/build.py runs this on the app folder after PyInstaller, and stops the build on a find.
"""

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

# An editable install's metadata holds the builder's checkout path and build times (T-002), and a
# delvewheel-repaired wheel's DELVEWHEEL holds the paths of the machine that repaired it.
LOCAL_ONLY_FILES = frozenset({"direct_url.json", "uv_cache.json", "DELVEWHEEL"})

# Something in the app folder, by its name, with its bytes.
type Entry = tuple[str, bytes]


def bundle_files(folder: Path) -> Iterator[Path]:
    """Every file in the app folder, in a fixed order."""
    return (path for path in sorted(folder.rglob("*")) if path.is_file())


def is_local_only(name: str) -> bool:
    """Whether a file named in the bundle or in a RECORD holds the builder's paths or times."""
    return PureWindowsPath(name).name in LOCAL_ONLY_FILES or name.endswith(".pth")


def local_only_files(folder: Path) -> list[str]:
    """The metadata files, and the RECORD lines for them, that would say where the app was built."""
    found = []
    for path in bundle_files(folder):
        name = str(path.relative_to(folder))
        if is_local_only(path.name):
            found.append(name)
        elif path.name == "RECORD" and path.parent.name.endswith(".dist-info"):
            # Each line is "<path>,<hash>,<size>", and the size and hash can confirm a guess.
            listed = [line.split(",")[0] for line in path.read_text(encoding="utf-8").splitlines()]
            found.extend(f"{name} line for {entry}" for entry in listed if is_local_only(entry))
    return found


def path_spellings(path: str) -> list[str]:
    """A path as a file can hold it, casefolded: either slash, doubled backslashes, URL-encoded."""
    windows = PureWindowsPath(path)
    drive, *names = windows.as_posix().split("/")
    url = "/".join([drive, *(quote(name) for name in names)])
    forms = {str(windows), str(windows).replace("\\", "\\\\"), windows.as_posix(), url}
    return sorted(form.casefold() for form in forms)


def decoded_texts(data: bytes) -> Iterator[str]:
    """The bytes read as UTF-8, and as UTF-16 starting at either byte, casefolded."""
    yield data.decode("utf-8", "replace").casefold()
    yield data.decode("utf-16-le", "replace").casefold()
    yield data[1:].decode("utf-16-le", "replace").casefold()


def packed_entries(exe: Path) -> Iterator[Entry]:
    """What PyInstaller packs into the exe, decompressed: its archive, and the PYZ of modules."""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, CArchiveReader

    archive = CArchiveReader(str(exe))
    for name, (*_, typecode) in archive.toc.items():
        if typecode != PKG_ITEM_PYZ:
            yield f"{exe.name}!{name}", archive.extract(name)
            continue
        modules = archive.open_embedded_archive(name)
        for module in modules.toc:
            # Raw is the marshalled code, whose strings are UTF-8; a namespace package has none.
            data = modules.extract(module, raw=True)
            if data is not None:
                yield f"{exe.name}!{name}!{module}", data


def bundle_entries(folder: Path, exe: Path) -> Iterator[Entry]:
    """Every file in the app folder with its bytes, then what's packed inside the exe."""
    for path in bundle_files(folder):
        yield str(path.relative_to(folder)), path.read_bytes()
    yield from packed_entries(exe)


def entries_naming(entries: Iterable[Entry], private_paths: Sequence[str]) -> list[str]:
    """The names of the entries that hold any of the private paths, in any letter case."""
    spellings = [spelling for path in private_paths for spelling in path_spellings(path)]
    return [
        name
        for name, data in entries
        if any(spelling in text for text in decoded_texts(data) for spelling in spellings)
    ]


def leaks(folder: Path, exe: Path, private_paths: Sequence[str]) -> list[str]:
    """What in the app folder would say where it was built, or nothing when it's clean."""
    return local_only_files(folder) or entries_naming(bundle_entries(folder, exe), private_paths)
