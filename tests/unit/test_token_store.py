"""Tests for keeping the sign-in in the keyring, or in auth.json when the keyring fails."""

import importlib.util
import json
import math
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from keyring.errors import KeyringLocked, NoKeyringError

from scmp3.core import token_store
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.filelock import FileLock
from scmp3.core.token_store import Keyring, TokenStore
from scmp3.core.tokens import Tokens


def load_signin_fakes() -> ModuleType:
    """tests/fixtures/signin_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_signin_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "signin_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_signin_fakes()
make_access = fakes.make_access
make_tokens = fakes.make_tokens
signed_in = fakes.signed_in
FakeKeyring = fakes.FakeKeyring
BrokenKeyring = fakes.BrokenKeyring
CredError = fakes.CredError
CREDENTIAL_BLOB_LIMIT = fakes.CREDENTIAL_BLOB_LIMIT
CredentialManagerKeyring = fakes.CredentialManagerKeyring
CountingKeyring = fakes.CountingKeyring


class HalfKeyring(FakeKeyring):
    """Takes the first entry written, then fails, like a keyring that breaks halfway."""

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.entries:
            raise CredError("the second write failed")
        super().set_password(service, username, password)


class SimulatedCrash(BaseException):
    """Stands in for the process dying, which no except clause in the store catches."""


class CrashingKeyring(FakeKeyring):
    """Dies while writing the entry named in `crash_on`, once that's set."""

    crash_on: str | None = None

    def set_password(self, service: str, username: str, password: str) -> None:
        if username == self.crash_on:
            raise SimulatedCrash
        super().set_password(service, username, password)


def at(moment: float) -> Callable[[], float]:
    """A clock stopped at `moment`."""
    return lambda: moment


def fail_to_remove_auth_json(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    """Make deleting auth.json raise `error`, as a held file or a crash at that moment would."""
    real_unlink = Path.unlink

    def unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "auth.json":
            raise error
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def save_while_the_keyring_refuses(tokens: Tokens, *, at: float) -> None:
    """Save `tokens` the way a run whose keyring was locked does, so they go in auth.json."""
    TokenStore(BrokenKeyring(KeyringLocked("locked")), clock=lambda: at).save(tokens)
    # A later run starts out trying the keyring again.
    TokenStore.try_keyring_again()


def write_file(path: Path, tokens: Tokens, saved_at: object) -> None:
    """Write auth.json by hand, with any saved_at value, or none when it's None."""
    stored: dict[str, object] = {
        "access_token": tokens.access,
        "refresh_token": tokens.refresh,
        "client_id": tokens.client_id,
    }
    if saved_at is not None:
        stored["saved_at"] = saved_at
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored), encoding="utf-8")


def write_keyring(backend: FakeKeyring, tokens: Tokens, saved_at: object) -> None:
    """Write both keyring entries by hand, with any saved_at value, or none when it's None."""
    entry: dict[str, object] = {"refresh_token": tokens.refresh, "client_id": tokens.client_id}
    if saved_at is not None:
        entry["saved_at"] = saved_at
    backend.set_password("sc-mp3", token_store.REFRESH_ENTRY, json.dumps(entry))
    backend.set_password("sc-mp3", token_store.ACCESS_ENTRY, tokens.access)


def every_value(*pairs: Tokens) -> list[str]:
    return [
        value for tokens in pairs for value in (tokens.access, tokens.refresh, tokens.client_id)
    ]


@pytest.fixture
def backend() -> FakeKeyring:
    return FakeKeyring()


@pytest.fixture
def store(backend: FakeKeyring) -> TokenStore:
    return TokenStore(backend)


@pytest.fixture
def auth_file(scmp3_home: Path) -> Path:
    return scmp3_home / "config" / "auth.json"


# Storing


def test_the_keyring_holds_the_refresh_token_with_the_client_id_and_the_access_token_apart(
    backend: FakeKeyring, auth_file: Path
) -> None:
    store = TokenStore(backend, clock=at(1_800_000_000.5))
    tokens = make_tokens()
    store.save(tokens)
    refresh_entry = backend.entries[("sc-mp3", token_store.REFRESH_ENTRY)]
    assert json.loads(refresh_entry) == {
        "refresh_token": tokens.refresh,
        "client_id": tokens.client_id,
        "saved_at": 1_800_000_000.5,
    }
    assert backend.entries[("sc-mp3", token_store.ACCESS_ENTRY)] == tokens.access
    assert len(backend.entries) == 2
    assert store.location == "keyring"
    assert not auth_file.exists()


def test_a_sign_in_in_the_keyring_loads_back(backend: FakeKeyring) -> None:
    tokens = make_tokens()
    TokenStore(backend).save(tokens)
    store = TokenStore(backend)
    assert store.load() == tokens
    assert store.location == "keyring"


def test_nobody_is_signed_in_when_nothing_is_stored(store: TokenStore) -> None:
    assert store.load() is None
    assert store.location is None


def test_delete_removes_both_keyring_entries(store: TokenStore, backend: FakeKeyring) -> None:
    signed_in(store)
    store.delete()
    assert backend.entries == {}
    assert store.load() is None
    assert store.location is None


def test_delete_with_nothing_stored_is_fine(store: TokenStore) -> None:
    store.delete()
    assert store.load() is None


@pytest.mark.parametrize(
    "error",
    [NoKeyringError("no backend"), KeyringLocked("locked"), CredError(5, "CredWrite", "denied")],
    ids=["no keyring", "locked", "Windows error"],
)
def test_when_the_keyring_fails_the_sign_in_goes_in_a_file(
    error: Exception, auth_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = TokenStore(BrokenKeyring(error), clock=at(1_800_000_000.5))
    tokens = make_tokens()
    store.save(tokens)
    assert store.location == "file"
    assert json.loads(auth_file.read_text(encoding="utf-8")) == {
        "access_token": tokens.access,
        "refresh_token": tokens.refresh,
        "client_id": tokens.client_id,
        # Just after the keyring's try, so a keyring that took half the pair loses to the file.
        "saved_at": math.nextafter(1_800_000_000.5, math.inf),
    }
    assert "a file is used" in caplog.text
    reopened = TokenStore(BrokenKeyring(error))
    assert reopened.load() == tokens
    assert reopened.location == "file"


def test_delete_removes_the_file(auth_file: Path) -> None:
    store = TokenStore(BrokenKeyring(NoKeyringError("no backend")))
    store.save(make_tokens())
    store.delete()
    assert not auth_file.exists()
    assert store.load() is None


def test_a_keyring_save_removes_an_older_file(backend: FakeKeyring, auth_file: Path) -> None:
    save_while_the_keyring_refuses(make_tokens(), at=1.0)
    assert auth_file.exists()
    tokens = make_tokens()
    store = TokenStore(backend)
    store.save(tokens)
    assert not auth_file.exists()
    assert store.load() == tokens
    assert store.location == "keyring"


def test_a_file_left_by_a_refused_save_wins_over_an_older_keyring_pair(
    backend: FakeKeyring,
) -> None:
    TokenStore(backend, clock=at(1.0)).save(make_tokens())
    newer = make_tokens()
    save_while_the_keyring_refuses(newer, at=2.0)
    store = TokenStore(backend)
    assert store.load() == newer
    assert store.location == "file"


@pytest.mark.parametrize(
    ("file_saved_at", "winner"),
    [(1.0, "keyring"), (2.0, "keyring"), (3.0, "file")],
    ids=["file older", "tie", "file newer"],
)
def test_the_later_save_wins_and_a_tie_goes_to_the_keyring(
    file_saved_at: float, winner: str, backend: FakeKeyring, auth_file: Path
) -> None:
    in_keyring, in_file = make_tokens(), make_tokens()
    write_keyring(backend, in_keyring, 2.0)
    write_file(auth_file, in_file, file_saved_at)
    store = TokenStore(backend)
    assert store.load() == {"keyring": in_keyring, "file": in_file}[winner]
    assert store.location == winner


@pytest.mark.parametrize(
    "saved_at",
    [None, "yesterday", True, float("nan"), float("inf")],
    ids=["missing", "text", "bool", "NaN", "infinite"],
)
def test_a_file_without_a_usable_stamp_counts_as_the_oldest(
    saved_at: object, backend: FakeKeyring, auth_file: Path
) -> None:
    in_keyring, in_file = make_tokens(), make_tokens()
    write_keyring(backend, in_keyring, -1.0)
    write_file(auth_file, in_file, saved_at)
    assert TokenStore(backend).load() == in_keyring


@pytest.mark.parametrize(
    "saved_at",
    [None, "yesterday", float("nan"), float("inf")],
    ids=["missing", "text", "NaN", "infinite"],
)
def test_a_keyring_pair_without_a_usable_stamp_counts_as_the_oldest(
    saved_at: object, backend: FakeKeyring, auth_file: Path
) -> None:
    in_keyring, in_file = make_tokens(), make_tokens()
    write_keyring(backend, in_keyring, saved_at)
    write_file(auth_file, in_file, -1.0)
    store = TokenStore(backend)
    assert store.load() == in_file
    assert store.location == "file"


def test_two_pairs_without_stamps_go_to_the_keyring(backend: FakeKeyring, auth_file: Path) -> None:
    in_keyring, in_file = make_tokens(), make_tokens()
    write_keyring(backend, in_keyring, None)
    write_file(auth_file, in_file, None)
    assert TokenStore(backend).load() == in_keyring


def test_a_crash_before_the_old_file_is_removed_doesnt_let_it_win(
    backend: FakeKeyring, auth_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older, newer = make_tokens(), make_tokens()
    save_while_the_keyring_refuses(older, at=1.0)
    with monkeypatch.context() as patch:
        fail_to_remove_auth_json(patch, SimulatedCrash())
        with pytest.raises(SimulatedCrash):
            TokenStore(backend, clock=at(2.0)).save(newer)
    assert auth_file.exists()
    store = TokenStore(backend)
    assert store.load() == newer
    assert store.location == "keyring"
    # Loading removes the file the keyring replaced.
    assert not auth_file.exists()


def test_an_old_file_that_cant_be_removed_doesnt_win_and_is_logged_without_values(
    backend: FakeKeyring,
    auth_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    older, newer = make_tokens(), make_tokens()
    save_while_the_keyring_refuses(older, at=1.0)
    fail_to_remove_auth_json(monkeypatch, PermissionError(13, "The file is in use"))
    TokenStore(backend, clock=at(2.0)).save(newer)
    store = TokenStore(backend)
    assert store.load() == newer
    assert store.location == "keyring"
    assert auth_file.exists()
    assert caplog.text.count("An older sign-in is left in auth.json") == 2
    for value in every_value(older, newer):
        assert value not in caplog.text


def test_a_crash_between_the_keyring_writes_still_counts_as_the_newest_save(
    auth_file: Path,
) -> None:
    backend = CrashingKeyring()
    first, in_file, newest = make_tokens(), make_tokens(), make_tokens()
    TokenStore(backend, clock=at(1.0)).save(first)
    save_while_the_keyring_refuses(in_file, at=2.0)
    backend.crash_on = token_store.ACCESS_ENTRY
    with pytest.raises(SimulatedCrash):
        TokenStore(backend, clock=at(3.0)).save(newest)
    store = TokenStore(backend)
    # The new refresh token renews the old access token, so no refresh token is spent twice.
    assert store.load() == Tokens(first.access, newest.refresh, newest.client_id)
    assert store.location == "keyring"


def test_a_keyring_that_takes_only_the_refresh_entry_loses_to_the_file_it_fell_back_to(
    auth_file: Path,
) -> None:
    backend = CredentialManagerKeyring()
    TokenStore(backend, clock=at(1.0)).save(make_tokens())
    # Too big for Credential Manager, so the refresh entry is written and the access entry isn't.
    bigger = make_tokens(length=1300)
    TokenStore(backend, clock=at(2.0)).save(bigger)
    # A later run, which asks the keyring again.
    TokenStore.try_keyring_again()
    store = TokenStore(backend)
    assert store.load() == bigger
    assert store.location == "file"
    assert auth_file.exists()


def test_a_refused_keyring_isnt_asked_again_until_told_to_try_again() -> None:
    locked = CountingKeyring(BrokenKeyring(KeyringLocked("locked")))
    tokens = make_tokens()
    TokenStore(locked).save(tokens)
    asked = locked.calls
    for _ in range(5):
        assert TokenStore(locked).load() == tokens
    TokenStore(locked).save(make_tokens())
    assert locked.calls == asked
    TokenStore.try_keyring_again()
    TokenStore(locked).load()
    assert locked.calls > asked


def test_a_keyring_that_fails_a_read_is_skipped_while_the_file_holds_the_pair(
    auth_file: Path,
) -> None:
    locked = CountingKeyring(BrokenKeyring(KeyringLocked("locked")))
    tokens = make_tokens()
    write_file(auth_file, tokens, 1.0)
    assert TokenStore(locked).load() == tokens
    asked = locked.calls
    assert TokenStore(locked).load() == tokens
    assert locked.calls == asked


def test_with_no_file_the_keyring_is_asked_again_after_a_failed_read() -> None:
    locked = CountingKeyring(BrokenKeyring(KeyringLocked("locked")))
    TokenStore(locked).load()
    asked = locked.calls
    TokenStore(locked).load()
    assert locked.calls > asked


def test_an_older_file_stays_while_another_process_holds_auth_lock(
    backend: FakeKeyring, auth_file: Path
) -> None:
    older, newer = make_tokens(), make_tokens()
    write_file(auth_file, older, 1.0)
    write_keyring(backend, newer, 2.0)
    store = TokenStore(backend)
    # Once exclusive() has ended, this process no longer counts as holding auth.lock.
    with store.exclusive(must_hold=True):
        pass
    with FileLock(store.lock_file, 0):
        assert store.load() == newer
        assert auth_file.exists()
    assert store.load() == newer
    assert not auth_file.exists()


def test_a_load_inside_exclusive_removes_the_older_file_itself(
    backend: FakeKeyring, auth_file: Path
) -> None:
    write_file(auth_file, make_tokens(), 1.0)
    write_keyring(backend, make_tokens(), 2.0)
    store = TokenStore(backend)
    with store.exclusive(must_hold=True):
        store.load()
        assert not auth_file.exists()


def test_exclusive_takes_the_thread_lock_before_the_file_lock(store: TokenStore) -> None:
    entered = threading.Event()

    def change() -> None:
        with store.exclusive(must_hold=True):
            entered.set()

    with store.lock:
        waiter = threading.Thread(target=change)
        waiter.start()
        # Long enough for the waiter to reach store.lock and wait there.
        threading.Event().wait(0.3)
        # The waiter mustn't be holding auth.lock while it waits for store.lock.
        with FileLock(store.lock_file, 0):
            assert not entered.is_set()
    waiter.join(timeout=5)
    assert entered.is_set()


def test_a_keyring_that_fails_halfway_leaves_the_pair_in_the_file() -> None:
    store = TokenStore(HalfKeyring())
    tokens = make_tokens()
    store.save(tokens)
    assert store.location == "file"
    assert store.load() == tokens


def test_the_refresh_token_is_written_before_the_access_token() -> None:
    backend = HalfKeyring()
    TokenStore(backend).save(make_tokens())
    assert list(backend.entries) == [("sc-mp3", token_store.REFRESH_ENTRY)]


@pytest.mark.parametrize("text", ["{not json", "[]", '{"access_token": "x"}'])
def test_a_damaged_file_is_ignored_with_a_warning(
    text: str, store: TokenStore, auth_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text(text, encoding="utf-8")
    assert store.load() is None
    assert "auth.json doesn't hold a usable sign-in" in caplog.text


def test_a_damaged_keyring_entry_is_ignored_with_a_warning(
    store: TokenStore, backend: FakeKeyring, caplog: pytest.LogCaptureFixture
) -> None:
    backend.set_password("sc-mp3", token_store.REFRESH_ENTRY, "{not json")
    backend.set_password("sc-mp3", token_store.ACCESS_ENTRY, make_access())
    assert store.load() is None
    assert "can't be read" in caplog.text


def test_half_a_keyring_pair_counts_as_signed_out(store: TokenStore, backend: FakeKeyring) -> None:
    backend.set_password("sc-mp3", token_store.ACCESS_ENTRY, make_access())
    assert store.load() is None


def test_a_keyring_that_cant_be_read_counts_as_signed_out_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert TokenStore(BrokenKeyring(KeyringLocked("locked"))).load() is None
    assert "Can't read the sign-in from the keyring: KeyringLocked: locked" in caplog.text


def test_a_keyring_that_cant_delete_is_a_disk_error_after_the_file_is_gone(
    auth_file: Path,
) -> None:
    TokenStore(BrokenKeyring(NoKeyringError("no backend"))).save(make_tokens())
    store = TokenStore(BrokenKeyring(CredError(5, "CredDelete", "denied")))
    with pytest.raises(ScmpError) as error_info:
        store.delete()
    assert error_info.value.kind is ErrorKind.disk
    assert "can't delete the sign-in from the keyring" in str(error_info.value)
    assert not auth_file.exists()


def test_delete_still_clears_the_keyring_when_the_file_cant_be_deleted(
    backend: FakeKeyring, auth_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_while_the_keyring_refuses(make_tokens(), at=1.0)
    fail_to_remove_auth_json(monkeypatch, PermissionError(13, "The file is in use"))
    store = TokenStore(backend, clock=at(2.0))
    store.save(make_tokens())
    with pytest.raises(ScmpError) as error_info:
        store.delete()
    assert error_info.value.kind is ErrorKind.disk
    assert "can't delete auth.json" in str(error_info.value)
    assert backend.entries == {}
    assert auth_file.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes")
def test_the_fallback_file_is_readable_only_by_its_owner(auth_file: Path) -> None:
    TokenStore(BrokenKeyring(NoKeyringError("no backend"))).save(make_tokens())
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600


def test_each_keyring_entry_fits_windows_credential_manager() -> None:
    backend = CredentialManagerKeyring()
    store = TokenStore(backend)
    store.save(make_tokens(length=1100))
    assert store.location == "keyring"
    for value in backend.entries.values():
        assert len(value.encode("utf-16-le")) <= CREDENTIAL_BLOB_LIMIT


def test_an_access_token_too_big_for_credential_manager_goes_in_the_file() -> None:
    store = TokenStore(CredentialManagerKeyring())
    tokens = make_tokens(length=1300)
    store.save(tokens)
    assert store.location == "file"
    assert store.load() == tokens


def test_a_store_made_without_a_backend_uses_keyrings_default(fake_keyring: Keyring) -> None:
    tokens = make_tokens()
    TokenStore().save(tokens)
    assert fake_keyring.get_password("sc-mp3", token_store.ACCESS_ENTRY) == tokens.access


# Keeping tokens secret


@pytest.mark.parametrize("where", ["keyring", "file"])
def test_a_loaded_sign_in_registers_both_tokens_and_the_client_id(
    where: str, backend: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring_backend = backend if where == "keyring" else BrokenKeyring(NoKeyringError("none"))
    tokens = make_tokens()
    TokenStore(keyring_backend).save(tokens)
    registered: list[str] = []
    monkeypatch.setattr(token_store, "register_secret", registered.append)
    TokenStore(keyring_backend).load()
    assert set(registered) == set(every_value(tokens))


def test_save_registers_every_value_before_the_keyring_sees_it(
    backend: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = make_tokens()
    registered: list[str] = []
    monkeypatch.setattr(token_store, "register_secret", registered.append)
    TokenStore(backend).save(tokens)
    assert set(registered) == set(every_value(tokens))


def test_a_keyring_error_that_quotes_the_values_is_logged_without_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Values nobody has registered yet, so only save's own registration can mask them.
    tokens = make_tokens()
    error = CredError(f"refused {tokens.refresh} for {tokens.client_id} with {tokens.access}")
    TokenStore(BrokenKeyring(error)).save(tokens)
    assert "a file is used" in caplog.text
    for value in every_value(tokens):
        assert value not in caplog.text
