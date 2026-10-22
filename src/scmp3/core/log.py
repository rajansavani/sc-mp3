"""Logging to a rotating file in the logs folder, with tokens masked in every record.

The home folder shows as ~ in every record too, so a shared log never names the user.
"""

import logging
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePath
from urllib.parse import quote, urlsplit

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError

LOG_FILE = "sc-mp3.log"
MAX_BYTES = 1_000_000
FILE_COUNT = 5  # the current file plus four rotated ones
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MASK = "[redacted]"

# Our handlers carry these names, so setting up again replaces them instead of adding more.
FILE_HANDLER = "scmp3-file"
CONSOLE_HANDLER = "scmp3-console"

# Registered secrets shorter than this are ignored, so junk input can't mask ordinary words.
MIN_SECRET_LENGTH = 8

# A secret value ends at whitespace, a quote, or a separator between query or cookie values.
_VALUE = r"[^\s\"'&;,]+"

# Each pattern with its replacement. Where a group is kept, only the value after it is masked.
TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # The whole value of an Authorization, Cookie or Set-Cookie header, whatever the scheme.
    (
        re.compile(
            r"\b((?:Authorization|Set-Cookie|Cookie)[\"']?\s*[:=]\s*[\"']?)[^\"'\r\n&]+",
            re.IGNORECASE,
        ),
        rf"\1{MASK}",
    ),
    # Any key ending in "token", and passwords: oauth_token=, "access_token": and the like.
    (
        re.compile(
            rf"\b([\w-]*(?:token|password)[\"']?\s*(?:[:=]|%3D)\s*[\"']?){_VALUE}",
            re.IGNORECASE,
        ),
        rf"\1{MASK}",
    ),
    # "OAuth <value>" and "Bearer <value>", also with the space encoded as %20 or +.
    (re.compile(rf"\b((?:OAuth|Bearer)(?:\s+|%20|\+)){_VALUE}", re.IGNORECASE), rf"\1{MASK}"),
    # The secret part of a share link, /s-XXXX, also with the slash encoded as %2F.
    (re.compile(r"((?:/|%2F)s-)[A-Za-z0-9]{5,}", re.IGNORECASE), rf"\1{MASK}"),
    # SoundCloud's current token, a JWT: three base64url parts, the first starting with eyJ.
    (re.compile(r"(?<![A-Za-z0-9])eyJ[\w-]+\.[\w-]+\.[\w-]*", re.ASCII), MASK),
    # SoundCloud's older token shape: 2-<digits>-<digits>-<letters and digits>.
    (re.compile(r"(?<![A-Za-z0-9])2-\d+-\d+-[A-Za-z0-9]{4,}"), MASK),
)

# What the home folder becomes in logs.
HOME_MARK = "~"
# A slash or backslash, or a run of them, as a repr's doubled backslashes give.
_SEPARATORS = r"[\\/]+"
# What can't follow the home folder's last part, so C:\Users\kai never matches C:\Users\kaia.
_NAME_GOES_ON = r"(?![\w.-])"
_DRIVE_LETTER = re.compile(r"[A-Za-z]:")
_NEVER = re.compile(r"(?!)")

# Exact values to mask, longest first, so a secret that contains another is masked whole.
_secrets: tuple[str, ...] = ()
_secrets_lock = threading.Lock()


def home_pattern(
    home: PurePath,
    *,
    ignore_case: bool = sys.platform == "win32",
    short_home: PurePath | None = None,
) -> re.Pattern[str]:
    """A pattern for `home` in text, with either slash, doubled backslashes, or no drive letter.

    It also matches the folder names URL-encoded, as in a file: link, and Windows' 8.3 short form
    when there is one, such as JOHNSM~1. Windows ignores case in paths, so the pattern does too
    there.
    """
    anchor, *names = home.parts
    if not names:
        # A home at the root of a drive would turn every path into ~.
        return _NEVER
    spellings = [names, [quote(name) for name in names]]
    if short_home is not None:
        spellings.append(list(short_home.parts[1:]))
    bodies = dict.fromkeys(
        "".join(_SEPARATORS + re.escape(name) for name in spelling) for spelling in spellings
    )
    body = f"(?:{'|'.join(bodies)})"
    root = anchor.rstrip("\\/")
    # A drive letter can be left out, as in \Users\kaia, but a network share can't.
    if _DRIVE_LETTER.fullmatch(root):
        body = f"(?:{re.escape(root)})?{body}"
    elif root:
        body = re.escape(root) + body
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(body + _NAME_GOES_ON, flags)


def short_path(path: Path) -> PurePath | None:
    """Windows' 8.3 short form of a path, or None elsewhere or when it's the same."""
    if sys.platform != "win32":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    if not 0 < length < len(buffer) or buffer.value == str(path):
        return None
    return PurePath(buffer.value)


# Set once, from the user running sc-mp3. Tests replace it with a home of their own.
_home: re.Pattern[str] = home_pattern(Path.home(), short_home=short_path(Path.home()))

# Formats exception text the same way logging.Formatter does by default.
_traceback_formatter = logging.Formatter()


def register_secret(value: str) -> None:
    """Mask this exact value in every log record and every `redact` call from now on.

    Values shorter than MIN_SECRET_LENGTH are ignored, since no real token is that short.
    """
    global _secrets
    secret = value.strip()
    if len(secret) < MIN_SECRET_LENGTH:
        return
    with _secrets_lock:
        _secrets = tuple(sorted({*_secrets, secret}, key=len, reverse=True))


def redact(text: str) -> str:
    """Mask secrets, auth headers, token values, share links and token shapes, and the home folder.

    The home folder becomes ~, so a path keeps the folders below it.
    """
    for secret in _secrets:
        text = text.replace(secret, MASK)
    for pattern, replacement in TOKEN_PATTERNS:
        text = pattern.sub(replacement, text)
    return _home.sub(HOME_MARK, text)


def safe_url(url: str) -> str:
    """A URL's host, port and path, for logs and error details.

    The query can hold ids and share tokens, so it's left out, and so are any user name and
    password.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return f"{host}{parts.path}"


class RedactFilter(logging.Filter):
    """Masks secrets in a record's message, its arguments, and any exception or stack text."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Merge the arguments first, so a token passed as an argument is masked too.
        record.msg = redact(_message(record))
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = _traceback_formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _message(record: logging.LogRecord) -> str:
    """The record's message with its arguments merged in."""
    try:
        return record.getMessage()
    except Exception:
        # A filter's exception reaches the code that logged, so a bad record must never raise.
        return _fallback_message(record)


def _fallback_message(record: logging.LogRecord) -> str:
    """The format string and the arguments side by side, when they don't make a message."""
    try:
        return f"{record.msg} {record.args!r}"
    except Exception:
        return f"(a log message that couldn't be printed, from {record.module}:{record.lineno})"


def setup_logging(console: bool = False, level: int = logging.INFO) -> Path:
    """Log to a rotating file in the logs folder, and to stderr too when `console` is set.

    Returns the log file's path. Calling it again replaces the handlers it added before.
    """
    close_logging()
    log_file = paths.log_dir() / LOG_FILE
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=MAX_BYTES, backupCount=FILE_COUNT - 1, encoding="utf-8"
        )
    except OSError as error:
        raise ScmpError(
            ErrorKind.disk, f"can't open the log file: {error.strerror or error}"
        ) from error

    file_handler.set_name(FILE_HANDLER)
    handlers: list[logging.Handler] = [file_handler]
    # A windowed build has no stderr, so there's nothing to log to.
    if console and sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.set_name(CONSOLE_HANDLER)
        handlers.append(console_handler)

    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(logging.Formatter(FORMAT))
        # On the handler rather than a logger, so records from every logger pass through it.
        handler.addFilter(RedactFilter())
        root.addHandler(handler)
    root.setLevel(level)
    return log_file


def close_logging() -> None:
    """Remove and close the handlers `setup_logging` added."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() in (FILE_HANDLER, CONSOLE_HANDLER):
            root.removeHandler(handler)
            handler.close()
