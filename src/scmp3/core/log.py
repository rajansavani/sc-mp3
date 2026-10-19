"""Logging to a rotating file in the logs folder, with tokens masked in every record."""

import logging
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

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

# Exact values to mask, longest first, so a secret that contains another is masked whole.
_secrets: tuple[str, ...] = ()
_secrets_lock = threading.Lock()

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
    """Mask registered secrets, auth headers, token values, share links and token shapes."""
    for secret in _secrets:
        text = text.replace(secret, MASK)
    for pattern, replacement in TOKEN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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
