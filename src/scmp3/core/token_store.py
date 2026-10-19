"""Keeps the SoundCloud sign-in in the system keyring, or in <config>/auth.json when that fails."""

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.filelock import FileLock, LockTimeout, LockUnavailable
from scmp3.core.log import redact, register_secret
from scmp3.core.tokens import Tokens, is_text

KEYRING_SERVICE = "sc-mp3"
REFRESH_ENTRY = "soundcloud-refresh-token"  # holds the client_id and saved_at too
ACCESS_ENTRY = "soundcloud-access-token"
FILE_NAME = "auth.json"
FILE_MODE = 0o600
# Every sc-mp3 process, the app and the CLI alike, changes the stored pair only while holding
# this file's lock, through TokenStore.exclusive.
AUTH_LOCK_FILE = "auth.lock"
# Longer than a renewal takes at the 30-second socket timeout.
AUTH_LOCK_TIMEOUT = 45  # seconds
SAVED_AT = "saved_at"  # when the pair was saved, in UTC seconds since the epoch
# A missing or unreadable stamp counts as older than any real one.
OLDEST = float("-inf")

StoreLocation = Literal["keyring", "file"]
Clock = Callable[[], float]

log = logging.getLogger(__name__)


class Keyring(Protocol):
    """The parts of a keyring backend the store uses. keyring.get_keyring() returns one."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class _Saved:
    """A stored pair and when it was saved."""

    tokens: Tokens
    saved_at: float


class TokenStore:
    """Keeps the sign-in in the system keyring, or in <config>/auth.json when the keyring fails.

    The keyring holds two entries under the service sc-mp3: the refresh token with the client_id
    and the time it was saved, and the access token.
    """

    # One reentrant lock for every store in the process, since they share the same keyring
    # entries and file, and renewing saves while holding it.
    lock = threading.RLock()
    # Set for the whole process once the keyring refuses or fails, so while the pair is in
    # auth.json a locked keyring isn't asked, and doesn't prompt, on every job.
    keyring_refused = False
    # True while this process holds auth.lock, which only one thread at a time can, under `lock`.
    _holding_auth_lock = False

    def __init__(self, backend: Keyring | None = None, *, clock: Clock = time.time) -> None:
        self._keyring = backend if backend is not None else keyring.get_keyring()
        self._file = paths.config_dir() / FILE_NAME
        self.lock_file = paths.config_dir() / AUTH_LOCK_FILE
        self._clock = clock
        # Where the stored sign-in is, as the last load or save found. None when there's none.
        self.location: StoreLocation | None = None

    @staticmethod
    def try_keyring_again() -> None:
        """Let the next load and save use the keyring again, as a sign-in or sign-out does."""
        TokenStore.keyring_refused = False

    @contextmanager
    def exclusive(self, *, must_hold: bool) -> Iterator[None]:
        """Hold `lock` and then auth.lock, so no other thread or sc-mp3 process changes the pair.

        When another process keeps auth.lock past the timeout, this raises offline if it
        `must_hold` it, and otherwise goes ahead with `lock` alone, as it does where file locks
        don't work. The order never changes, so the two locks can't deadlock.
        """
        with self.lock:
            file_lock = FileLock(self.lock_file, AUTH_LOCK_TIMEOUT)
            TokenStore._holding_auth_lock = _take(file_lock, must_hold=must_hold)
            try:
                yield
            finally:
                TokenStore._holding_auth_lock = False
                # This does nothing when the lock wasn't taken.
                file_lock.release()

    def load(self) -> Tokens | None:
        """The stored sign-in, or None when nobody is signed in.

        When the keyring and the file both hold a pair, the later save wins, and a tie goes to
        the keyring. A keyring that refused is skipped only while the file holds a pair.
        """
        with self.lock:
            in_file = self._load_file()
            # With no file, the keyring is where the sign-in is, so one failed read can't hide it.
            skip_keyring = TokenStore.keyring_refused and in_file is not None
            in_keyring = None if skip_keyring else self._load_keyring()
            if _file_is_newer(in_file, in_keyring):
                self.location = "file"
                return in_file.tokens
            if in_keyring is None:
                self.location = None
                return None
            if in_file is not None:
                self._remove_old_file()
            self.location = "keyring"
            return in_keyring.tokens

    def save(self, tokens: Tokens) -> None:
        """Store both tokens in one step: in the keyring, or in the file when the keyring fails."""
        # Registered first, since a backend's error message could quote them.
        for value in (tokens.access, tokens.refresh, tokens.client_id):
            register_secret(value)
        with self.lock:
            saved_at = self._clock()
            if TokenStore.keyring_refused or not self._save_keyring(tokens, saved_at):
                # Strictly later than the keyring's try, so a keyring that took only the refresh
                # entry loses to this file at the next load.
                self._save_file(tokens, math.nextafter(saved_at, math.inf))
                self.location = "file"
                return
            self.location = "keyring"
            self._remove_old_file()

    def delete(self) -> None:
        """Delete the file and both keyring entries, trying each even when the other fails."""
        with self.lock:
            self.location = None
            file_failure = _failure_of(self._remove_file)
            keyring_failure = _failure_of(self._delete_keyring)
        if file_failure is None and keyring_failure is None:
            return
        if file_failure is not None and keyring_failure is not None:
            log.warning("The keyring entries couldn't be deleted either: %s", keyring_failure)
        raise file_failure or keyring_failure

    def _load_keyring(self) -> _Saved | None:
        try:
            refresh_entry = self._keyring.get_password(KEYRING_SERVICE, REFRESH_ENTRY)
            access = self._keyring.get_password(KEYRING_SERVICE, ACCESS_ENTRY)
        except NoKeyringError:
            log.debug("There's no keyring on this system to read a sign-in from")
            TokenStore.keyring_refused = True
            return None
        except Exception as error:
            log.warning("Can't read the sign-in from the keyring: %s", _say(error))
            TokenStore.keyring_refused = True
            return None
        if refresh_entry is None or access is None:
            return None
        register_secret(access)
        saved = _saved_from_json(refresh_entry, access=access)
        if saved is None:
            log.warning("The sign-in in the keyring can't be read, so it's ignored")
        return saved

    def _save_keyring(self, tokens: Tokens, saved_at: float) -> bool:
        """Write both entries. Returns False, with a warning, when the keyring refuses."""
        refresh_entry = json.dumps(
            {"refresh_token": tokens.refresh, "client_id": tokens.client_id, SAVED_AT: saved_at}
        )
        try:
            # Refresh entry first, with the stamp: if the second write fails, this still counts as
            # the newest save, and its old access token can be renewed with the new refresh token.
            self._keyring.set_password(KEYRING_SERVICE, REFRESH_ENTRY, refresh_entry)
            self._keyring.set_password(KEYRING_SERVICE, ACCESS_ENTRY, tokens.access)
        # Each backend raises its own errors, such as pywintypes.error on Windows.
        except Exception as error:
            log.warning("The keyring can't store the sign-in, so a file is used: %s", _say(error))
            TokenStore.keyring_refused = True
            return False
        return True

    def _delete_keyring(self) -> None:
        for entry in (REFRESH_ENTRY, ACCESS_ENTRY):
            try:
                self._keyring.delete_password(KEYRING_SERVICE, entry)
            except PasswordDeleteError:
                log.debug("The keyring had no %s entry to delete", entry)
            except NoKeyringError:
                log.debug("There's no keyring on this system, so nothing is stored there")
                return
            except Exception as error:
                detail = f"can't delete the sign-in from the keyring: {_say(error)}"
                # Not chained, since the error's own text could hold a token that the detail masks.
                raise ScmpError(ErrorKind.disk, detail) from None

    def _load_file(self) -> _Saved | None:
        try:
            text = self._file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            log.warning(
                "Can't read %s, so the keyring is tried: %s",
                FILE_NAME,
                error.strerror or type(error).__name__,
            )
            return None
        saved = _saved_from_json(text)
        if saved is None:
            log.warning("%s doesn't hold a usable sign-in, so it's ignored", FILE_NAME)
        return saved

    def _save_file(self, tokens: Tokens, saved_at: float) -> None:
        stored = {
            "access_token": tokens.access,
            "refresh_token": tokens.refresh,
            "client_id": tokens.client_id,
            SAVED_AT: saved_at,
        }
        # The temp file this writes is already private, so the tokens are never readable by others.
        paths.write_text_atomically(self._file, json.dumps(stored))
        try:
            # Set again, so the file stays private even if the helper above ever changes.
            os.chmod(self._file, FILE_MODE)
        except OSError as error:
            detail = f"can't make {FILE_NAME} private: {error.strerror or type(error).__name__}"
            raise ScmpError(ErrorKind.disk, detail) from error

    def _remove_old_file(self) -> None:
        """Delete an auth.json the keyring's newer save replaced, but only under auth.lock.

        Another process may be writing auth.json while it holds the lock, so a busy lock leaves
        the file for the next load.
        """
        if TokenStore._holding_auth_lock:
            self._remove_file_or_warn()
            return
        file_lock = FileLock(self.lock_file, 0)
        try:
            file_lock.acquire()
        except (LockTimeout, LockUnavailable):
            log.debug("%s is held, so the older %s stays for now", AUTH_LOCK_FILE, FILE_NAME)
            return
        try:
            self._remove_file_or_warn()
        finally:
            file_lock.release()

    def _remove_file_or_warn(self) -> None:
        try:
            self._remove_file()
        except ScmpError as error:
            # Loading picks the keyring's newer pair anyway, so this only leaves a stale file.
            log.warning("An older sign-in is left in %s: %s", FILE_NAME, error)

    def _remove_file(self) -> None:
        try:
            self._file.unlink(missing_ok=True)
        except OSError as error:
            raise ScmpError(
                ErrorKind.disk,
                f"can't delete {FILE_NAME}: {error.strerror or type(error).__name__}",
            ) from error


def _file_is_newer(in_file: _Saved | None, in_keyring: _Saved | None) -> bool:
    """True when only the file holds a pair, or its save is strictly the later one."""
    if in_file is None:
        return False
    return in_keyring is None or in_file.saved_at > in_keyring.saved_at


def _saved_from_json(text: str, access: str | None = None) -> _Saved | None:
    """A stored pair from a JSON object, or None when a part is missing.

    The keyring keeps the access token in an entry of its own, so it's passed in.
    """
    try:
        stored = json.loads(text)
    except ValueError:
        # The caller logs that the stored sign-in can't be read.
        return None
    if not isinstance(stored, dict):
        return None
    if access is None:
        access = stored.get("access_token")
    refresh = stored.get("refresh_token")
    client_id = stored.get("client_id")
    if not (is_text(access) and is_text(refresh) and is_text(client_id)):
        return None
    for value in (access, refresh, client_id):
        register_secret(value)
    tokens = Tokens(access=access, refresh=refresh, client_id=client_id)
    return _Saved(tokens, _stamp(stored.get(SAVED_AT)))


def _stamp(value: Any) -> float:
    """A saved_at value as a number, or OLDEST when it's missing or isn't a finite number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return OLDEST
    if not math.isfinite(value):
        return OLDEST
    return float(value)


def _take(file_lock: FileLock, *, must_hold: bool) -> bool:
    """Take auth.lock and return True, or return False when going ahead without it is allowed."""
    try:
        file_lock.acquire()
    except LockTimeout as error:
        if must_hold:
            # Counted as a network failure, so a token that still works carries on until exp.
            detail = "another sc-mp3 process is changing the SoundCloud sign-in and hasn't finished"
            raise ScmpError(ErrorKind.offline, detail) from error
        log.warning("Another sc-mp3 process kept auth.lock too long, so this goes ahead")
        return False
    except LockUnavailable as error:
        # On an NFS home with no lock service, for example, only TokenStore.lock is left.
        log.warning("auth.lock can't be used, so only this process's own lock applies: %s", error)
        return False
    return True


def _failure_of(action: Callable[[], None]) -> ScmpError | None:
    """Run `action` and return the ScmpError it raised, so the next step still runs."""
    try:
        action()
    except ScmpError as error:
        return error
    return None


def _say(error: Exception) -> str:
    """An error's type and redacted message, for a log line or a detail."""
    return f"{type(error).__name__}: {redact(str(error))}"
