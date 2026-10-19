"""Turn pasted or dropped text into typed inputs: links, local files, or rejects with a reason.

Parsing never touches the network, and reads the disk only for local paths. Links are
recognized and normalized by scmp3.core.urls.
"""

import ctypes
import os
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scmp3.core.urls import NOT_SUPPORTED, LinkKind, UnsupportedInput, classify_link, is_link

type InputKind = LinkKind | Literal["local_file", "unsupported"]

# A paste is read up to these limits, so a huge one can't stall the caller.
MAX_ITEMS = 2_000  # links and paths, accepted or not; plain words don't count
MAX_CHARACTERS = 1_000_000
# Only this many plain words are listed as rejects, so chat text doesn't bury its links.
MAX_WORDS = 100

# Reasons are shown to the user next to the item that was rejected.
FILE_NOT_FOUND = "File not found. Check the path."
FOLDER_NOT_FILE = "Folders can't be added. Add the MP3 or M4A files inside the folder instead."
NOT_RETAGGABLE = "Only MP3 and M4A files can be re-tagged."
PASTE_TOO_LONG = (
    f"Only the first {MAX_ITEMS:,} links and files, up to {MAX_CHARACTERS:,} characters, "
    "were read. Add the rest in another paste."
)


@dataclass(frozen=True, slots=True)
class ParsedInput:
    """One pasted or dropped item and what it turned out to be."""

    kind: InputKind
    raw: str  # the item as it appeared in the text, quotes included; empty for PASTE_TOO_LONG
    url: str | None = None  # the normalized link, for SoundCloud and YouTube items
    path: Path | None = None  # the absolute path, for local files
    reason: str | None = None  # why an unsupported item was rejected, for the user


_RETAG_EXTENSIONS = frozenset({".mp3", ".m4a"})
# A drive, a share, a home or relative folder, or one root slash, on every OS.
_PATH_START = re.compile(r"[A-Za-z]:[\\/]|\\\\|~[\\/]|\.{1,2}[\\/]|/(?![\\/])")
# Two leading slashes of either kind make a network path on Windows.
_SHARE_START = re.compile(r"[\\/]{2}")
# Inside a network path: a quote or control character, which no path holds, or another path.
_ANOTHER_PATH = re.compile(r"[\x00-\x1f\x7f\"]|\s['\"]?(?:[A-Za-z]:)?[\\/]")
# Looking at a path under these roots mounts a network host: the /net automounter on macOS.
_NETWORK_ROOTS = ("/net/",) if sys.platform == "darwin" else ()
_DRIVE_LETTER = re.compile(r"[A-Za-z]:[\\/]")
_DRIVE_REMOTE = 4  # what GetDriveTypeW returns for a mapped network drive
_QUOTES = ('"', "'")
# A quoted run, or a run of anything but whitespace.
_TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')


def parse_inputs(text: str) -> list[ParsedInput]:
    """Classify every item in pasted or dropped text, in order, keeping the first of repeats.

    At most MAX_ITEMS links and files and MAX_CHARACTERS characters are read; past either, the
    result ends with one PASTE_TOO_LONG item. Only the first MAX_WORDS plain words are listed.
    """
    head = _cut_to_length(text, MAX_CHARACTERS)
    inputs, items_left_out = _collect(_split_items(head))
    if items_left_out or text[len(head) :].strip():
        inputs.append(ParsedInput("unsupported", "", reason=PASTE_TOO_LONG))
    return inputs


def classify(item: str) -> ParsedInput:
    """Work out what one item is: a link, a local file, or something unsupported."""
    try:
        return _classify(item)
    except UnsupportedInput as error:
        return ParsedInput("unsupported", item, reason=str(error))


def _classify(item: str) -> ParsedInput:
    """Classify one item, raising UnsupportedInput with the reason when it can't be used."""
    text = _unquote(item)
    if is_link(text):
        kind, url = classify_link(text)
        return ParsedInput(kind, item, url=url)
    path = _local_path(text)
    if path is None:
        raise UnsupportedInput(NOT_SUPPORTED)
    _check_retaggable(path)
    return ParsedInput("local_file", item, path=path)


def _collect(items: Iterable[str]) -> tuple[list[ParsedInput], bool]:
    """Classify items, keeping the first of repeats, MAX_ITEMS links and files and MAX_WORDS words.

    The flag says whether links or files past MAX_ITEMS were left out.
    """
    seen_items: set[str] = set()  # text already classified, so a repeated word costs nothing
    seen: set[tuple[str, str]] = set()
    kept: list[ParsedInput] = []
    counted = words = 0
    for item in items:
        if item in seen_items:
            continue
        seen_items.add(item)
        parsed = classify(item)
        identity = _identity(parsed)
        if identity in seen:
            continue
        seen.add(identity)
        if _is_plain_word(parsed):
            if words < MAX_WORDS:
                words += 1
                kept.append(parsed)
            continue
        if counted == MAX_ITEMS:
            return kept, True
        counted += 1
        kept.append(parsed)
    return kept, False


def _is_plain_word(parsed: ParsedInput) -> bool:
    """Whether a reject is neither a link nor a path, so it doesn't count toward MAX_ITEMS."""
    # Paths get the file reasons, so only words and links to other sites get NOT_SUPPORTED.
    return parsed.reason == NOT_SUPPORTED and not is_link(_unquote(parsed.raw))


def _identity(parsed: ParsedInput) -> tuple[str, str]:
    """What makes two items the same: the normalized link, the file, or else the text."""
    if parsed.url is not None:
        return ("url", parsed.url)
    if parsed.path is not None:
        return ("path", os.path.normcase(parsed.path))
    return ("text", _unquote(parsed.raw))


def _cut_to_length(text: str, limit: int) -> str:
    """Text up to limit characters, leaving out the item the limit falls inside."""
    head = text[:limit]
    if len(text) <= limit or text[limit].isspace() or head[-1].isspace():
        return head
    words = head.rsplit(maxsplit=1)
    return words[0] if len(words) == 2 else ""


def _split_items(text: str) -> Iterator[str]:
    """Yield a whole line when it names one path, else the line's words and quoted runs."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # A dropped or pasted path can contain spaces, so try the whole line before splitting it.
        if _is_one_path(stripped):
            yield stripped
            continue
        for token in _TOKEN.finditer(stripped):
            yield from _split_quoted_run(token[0])


def _is_one_path(line: str) -> bool:
    """Whether a whole line names one file or folder, reading the disk only for local paths."""
    text = _unquote(line)
    try:
        path = _local_path(text)
    except UnsupportedInput:
        return False  # a file: URI that can't be a path on this OS
    if path is None:
        return False
    if _is_network_path(text) or _is_network_path(str(path)):
        # Without asking the network, the line is one path unless it holds something else.
        return not _ANOTHER_PATH.search(text) and not _holds_link(text.split())
    # A file that exists is never a link, even when a word in its path looks like one.
    return os.path.exists(path)


def _split_quoted_run(token: str) -> list[str]:
    """Keep a quoted run whole unless it holds a link, since an apostrophe can open one."""
    words = token.split()
    if len(words) > 1 and not _is_one_path(token) and _holds_link(words):
        return words
    return [token]


def _unquote(text: str) -> str:
    """Drop one pair of matching quotes around text, as drag and drop or "Copy as path" add."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTES:
        return text[1:-1]
    return text


def _holds_link(words: Iterable[str]) -> bool:
    return any(is_link(word) for word in words)


def _local_path(text: str) -> Path | None:
    """The absolute path text names, when it's a file: URI or reads as a path. No disk access."""
    if not _can_be_path(text):
        return None
    if text[:5].lower() == "file:":
        return _path_from_uri(text)
    if not _looks_like_path(text):
        return None
    if text.startswith(("~/", "~\\")):
        text = os.path.expanduser(text)
    return Path(os.path.abspath(text))


def _can_be_path(text: str) -> bool:
    """False for text with NUL or lone surrogates, which no real path holds."""
    # expanduser raises on both on macOS and Linux.
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _path_from_uri(uri: str) -> Path:
    try:
        # from_uri only takes a lowercase scheme.
        path = Path.from_uri("file:" + uri[5:])
    except ValueError as error:
        # This includes a Windows drive URI read on macOS or Linux.
        raise UnsupportedInput(FILE_NOT_FOUND) from error
    return Path(os.path.abspath(path))


def _looks_like_path(text: str) -> bool:
    """Whether text reads as a path, so a missing one is "not found" rather than "not a link"."""
    # A protocol-relative link isn't a path, and on Windows looking it up reaches the network.
    if text.startswith("//"):
        return False
    if _PATH_START.match(text):
        return True
    # Most words have no dot, and building a Path for each would slow a long paste down.
    return "." in text and Path(text).suffix.lower() in _RETAG_EXTENSIONS


def _is_network_path(path: str) -> bool:
    """Whether looking at path could reach the network: a share, a mapped drive, or macOS /net."""
    if _SHARE_START.match(path) or path.startswith(_NETWORK_ROOTS):
        return True
    return _is_remote_drive(path)


def _is_remote_drive(path: str) -> bool:
    """Whether path is on a mapped network drive, which Windows knows without asking the drive."""
    if _get_drive_type is None or not _DRIVE_LETTER.match(path):
        return False
    return _get_drive_type(path[:2] + "\\") == _DRIVE_REMOTE


def _windows_drive_type() -> Callable[[str], int] | None:
    """GetDriveTypeW on Windows, which reads only local state; None on other systems."""
    if os.name != "nt":
        return None
    # A private copy of kernel32, so setting the argument types changes nothing shared.
    get_drive_type = ctypes.WinDLL("kernel32").GetDriveTypeW
    get_drive_type.argtypes = (ctypes.c_wchar_p,)
    get_drive_type.restype = ctypes.c_uint
    return get_drive_type


# Set once at import. Tests replace it, so no test asks Windows about a real drive.
_get_drive_type = _windows_drive_type()


def _check_retaggable(path: Path) -> None:
    """Raise UnsupportedInput unless path is an existing MP3 or M4A file, or one on the network."""
    is_audio = path.suffix.lower() in _RETAG_EXTENSIONS
    if _is_network_path(str(path)):
        # Looking at it can block for many seconds, so the job checks the file when it starts.
        if not is_audio:
            raise UnsupportedInput(NOT_RETAGGABLE)
        return
    if os.path.isdir(path):
        raise UnsupportedInput(FOLDER_NOT_FILE)
    if not os.path.isfile(path):
        raise UnsupportedInput(FILE_NOT_FOUND)
    if not is_audio:
        raise UnsupportedInput(NOT_RETAGGABLE)
