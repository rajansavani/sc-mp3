"""What the frozen Windows app checks before it opens: that its folder's path is short enough for
the files inside it, and that curl_cffi gets a certificate file it can open.

Both come from T-002's measurements of the PyInstaller build.
"""

import locale
import logging
import os
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path, PurePath

from scmp3.app import messages
from scmp3.app.message_box import MB_ICONWARNING, MB_OK, MessageBox, windows_message_box
from scmp3.core import paths
from scmp3.core.errors import ScmpError
from scmp3.core.log import setup_logging, short_path

# Past 250 UTF-16 units in a file's full path, curl_cffi's DLL doesn't load, and yt-dlp drops it
# without a word. The window and .NET fail a few units further on.
FILE_PATH_LIMIT = 250
# Room for what the measurements missed, such as a file one of the DLLs opens by itself.
PATH_MARGIN = 10
EXIT_FOLDER_TOO_LONG = 1

# curl_cffi takes its certificate file from the first of these that's set, and otherwise from
# certifi, whose copy is inside the app's folder.
CA_FILE_VARIABLES = ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE")
# The one the app sets. curl_cffi reads it for every request, and Python's ssl module ignores it.
CA_FILE_VARIABLE = "CURL_CA_BUNDLE"
CA_FILE_NAME = "cacert.pem"

log = logging.getLogger(__name__)

# Windows' 8.3 form of a path, or None when the volume has none.
type ShortPath = Callable[[Path], PurePath | None]


def utf16_units(text: str) -> int:
    """A path's length as Windows counts it, where a character outside the BMP counts twice."""
    return len(text.encode("utf-16-le")) // 2


def longest_relative_path(folder: Path) -> int:
    """The length of the longest file path inside `folder`, measured from it."""
    base = utf16_units(str(folder))
    longest = 0
    for directory, _, files in os.walk(folder, onerror=raise_error):
        for name in files:
            # The full path minus the folder and the separator after it.
            longest = max(longest, utf16_units(os.path.join(directory, name)) - base - 1)
    return longest


def raise_error(error: OSError) -> None:
    raise error


@dataclass(frozen=True)
class FolderCheck:
    """How long the path of the folder holding sc-mp3.exe is, and how long it can be."""

    folder: Path
    length: int
    limit: int

    @property
    def fits(self) -> bool:
        return self.length <= self.limit


def check_app_folder(contents: Path) -> FolderCheck:
    """Measure the app's folder from its contents folder (_internal), as the DLL loader would.

    pywebview finds its DLLs through the real path, so a junction or a mapped drive doesn't
    shorten anything, and the longer of the two paths counts.
    """
    real = Path(os.path.realpath(contents))
    # The message names the path that's measured, so a junction's target rather than the link.
    longer = max(contents, real, key=lambda path: utf16_units(str(path)))
    # The contents folder's own name and separator, which the user doesn't choose.
    inner = utf16_units(str(longer)) - utf16_units(str(longer.parent))
    room = FILE_PATH_LIMIT - PATH_MARGIN - 1 - longest_relative_path(real)
    return FolderCheck(longer.parent, utf16_units(str(longer)) - inner, room - inner)


def say_folder_too_long(check: FolderCheck, message_box: MessageBox | None = None) -> None:
    """Explain that the app has to move to a folder with a shorter path."""
    log.warning(
        "The app's folder path has %s characters, over the %s its files allow",
        check.length,
        check.limit,
    )
    text = messages.FOLDER_TOO_LONG.format(
        folder=check.folder, length=check.length, limit=check.limit
    )
    show = message_box or windows_message_box
    show(text, messages.FOLDER_TOO_LONG_TITLE, MB_OK | MB_ICONWARNING)


def ansi_code_page() -> str:
    """The code page libcurl opens files through, such as cp1252 on an English Windows."""
    return locale.getencoding()


def fits_code_page(path: PurePath, encoding: str) -> bool:
    """Whether every character of the path exists in the code page."""
    try:
        str(path).encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def copy_certificates(bundled: Path, folder: Path) -> Path:
    """Copy the certificate file into `folder`, unless an identical copy is there already."""
    target = folder / CA_FILE_NAME
    data = bundled.read_bytes()
    if not target.is_file() or target.read_bytes() != data:
        # certifi's file is UTF-8 with LF line ends, which this writes back byte for byte.
        paths.write_text_atomically(target, data.decode("utf-8"))
    return target


def readable_certificates(
    bundled: Path, copy_folder: Path, encoding: str, shorten: ShortPath
) -> PurePath | None:
    """A path to the certificates that the code page can spell, or None when there's none."""
    short = shorten(bundled)
    if short is not None and fits_code_page(short, encoding):
        return short
    # Volumes without 8.3 names, such as most second drives, need a copy somewhere else.
    try:
        copy = copy_certificates(bundled, copy_folder)
    except (OSError, ScmpError) as error:
        log.warning("Couldn't copy the certificates out of the app's folder: %s", error)
        return None
    for candidate in (copy, shorten(copy)):
        if candidate is not None and fits_code_page(candidate, encoding):
            return candidate
    return None


def prepare_certificates(
    bundled: Path,
    copy_folder: Path,
    *,
    environ: MutableMapping[str, str] = os.environ,
    encoding: str | None = None,
    shorten: ShortPath = short_path,
) -> PurePath | None:
    """Make sure curl_cffi gets a certificate file whose path it can pass to libcurl.

    libcurl opens the file through the ANSI code page, so in a folder named outside it, such as
    жук on an English Windows, every impersonated request fails with "HTTP Error 0" (T-002).
    Returns the path it set, or None when nothing needed changing.
    """
    if any(environ.get(name) for name in CA_FILE_VARIABLES):
        # The user chose a file, and curl_cffi reads it first.
        return None
    code_page = encoding or ansi_code_page()
    if fits_code_page(bundled, code_page):
        return None
    readable = readable_certificates(bundled, copy_folder, code_page, shorten)
    if readable is None:
        log.warning(
            "No path to the certificates fits the %s code page, so requests that impersonate a "
            "browser will fail. Moving sc-mp3 to a folder like C:\\sc-mp3 fixes that.",
            code_page,
        )
        return None
    environ[CA_FILE_VARIABLE] = str(readable)
    log.info(
        "The app's folder has characters outside %s, so curl_cffi gets another path", code_page
    )
    log.debug("curl_cffi's certificates: %s", readable)
    return readable


def bundled_certificates() -> Path:
    """certifi's file inside the app, which curl_cffi uses unless told otherwise."""
    import certifi

    return Path(certifi.where())


def point_certifi_at(path: PurePath) -> None:
    """Make certifi.where() return `path`, for code that hands it to curl_cffi itself."""
    import certifi

    location = str(path)

    def where() -> str:
        return location

    # yt-dlp passes certifi.where() to curl_cffi as a proxy's CA file on every proxied request.
    certifi.where = where


def windows_contents_folder(platform: str = sys.platform) -> Path | None:
    """The frozen Windows app's contents folder, or None when this isn't the frozen Windows app."""
    contents = getattr(sys, "_MEIPASS", None)
    if platform != "win32" or not getattr(sys, "frozen", False) or contents is None:
        return None
    return Path(contents)


def check_bundle(contents: Path, message_box: MessageBox | None = None) -> int | None:
    """Check the app's folder, then sort out the certificates. Returns an exit code to stop."""
    check = check_app_folder(contents)
    if not check.fits:
        say_folder_too_long(check, message_box)
        return EXIT_FOLDER_TOO_LONG
    readable = prepare_certificates(bundled_certificates(), paths.cache_dir())
    if readable is not None:
        point_certifi_at(readable)
    return None


def prepare() -> int | None:
    """Get the frozen Windows app ready to run. Returns an exit code when it can't."""
    contents = windows_contents_folder()
    if contents is None:
        return None
    # Set up here, so what the checks find reaches the log. The app sets it up again later.
    setup_logging()
    return check_bundle(contents)
