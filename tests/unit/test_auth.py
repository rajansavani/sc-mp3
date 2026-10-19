"""Tests for the sign-in flows, with a fake keyring and fake HTTP, so nothing leaves the machine."""

import errno
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qsl

import pytest
from keyring.errors import KeyringLocked
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE
from yt_dlp.networking.common import Request, Response
from yt_dlp.networking.exceptions import HTTPError, TransportError

from scmp3.core import auth, engine, filelock, paths, token_store
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.filelock import FileLock
from scmp3.core.models import Account
from scmp3.core.token_store import TokenStore
from scmp3.core.tokens import (
    TokenProblem,
    TokenRefused,
    Tokens,
    normalize_access,
    normalize_refresh,
    read_claims,
)


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
NOW = fakes.NOW
HOUR = fakes.HOUR
clock = fakes.clock
make_access = fakes.make_access
make_refresh = fakes.make_refresh
claim_client_id = fakes.claim_client_id
make_tokens = fakes.make_tokens
signed_in = fakes.signed_in
FakeKeyring = fakes.FakeKeyring
BrokenKeyring = fakes.BrokenKeyring
CredError = fakes.CredError
CountingKeyring = fakes.CountingKeyring
CredentialManagerKeyring = fakes.CredentialManagerKeyring


Outcome = bytes | Exception | Callable[[Request], bytes]


class FakeYdl:
    """Answers urlopen with canned outcomes in order, and records each request."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.requests: list[Request] = []
        self._outcomes = list(outcomes)

    def urlopen(self, request: Request) -> io.BytesIO:
        self.requests.append(request)
        assert self._outcomes, f"an unexpected request to {request.url}"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return io.BytesIO(outcome(request))
        return io.BytesIO(outcome)


def token_answer(access: str, refresh: str | None = None) -> bytes:
    """The token endpoint's answer to a renewal, as seen in T-003."""
    answer: dict[str, Any] = {
        "access_token": access,
        "expires_in": 86399,
        "scope": "",
        "token_type": "Bearer",
    }
    if refresh is not None:
        answer["refresh_token"] = refresh
    return json.dumps(answer).encode()


def oauth_error(code: str, status: int = 400) -> HTTPError:
    body = json.dumps({"error": code, "error_description": "made up"}).encode()
    return HTTPError(Response(io.BytesIO(body), auth.TOKEN_URL, {}, status=status))


def status_error(status: int, url: str = auth.TOKEN_URL) -> HTTPError:
    return HTTPError(Response(io.BytesIO(b""), url, {}, status=status))


PRIVATE_ME = {
    "primary_email": "kaia@example.com",
    "full_name": "Kaia Example",
    "date_of_birth": {"year": 1999, "month": 4, "day": 1},
}


def me_answer(product_id: str | None = "free", **fields: Any) -> bytes:
    """A /me answer with the private fields the real one has."""
    answer: dict[str, Any] = {
        "username": "Kaia",
        "permalink": "kaia-music",
        "avatar_url": "https://i1.sndcdn.com/avatars-000-abc-large.jpg",
        "consumer_subscription": {"product": {"id": product_id}},
        **PRIVATE_ME,
        **fields,
    }
    return json.dumps(answer).encode()


def form_of(request: Request) -> dict[str, str]:
    assert isinstance(request.data, bytes)
    return dict(parse_qsl(request.data.decode()))


@pytest.fixture(autouse=True)
def ytdlp_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fresh dict for the headers yt-dlp's SoundCloud extractors share, put back afterwards."""
    headers: dict[str, str] = {}
    monkeypatch.setattr(SoundcloudBaseIE, "_HEADERS", headers)
    return headers


@pytest.fixture
def backend() -> FakeKeyring:
    return FakeKeyring()


@pytest.fixture
def store(backend: FakeKeyring) -> TokenStore:
    return TokenStore(backend)


# Renewing


def test_renewing_sends_the_refresh_token_with_the_access_tokens_client_id(
    store: TokenStore,
) -> None:
    tokens = signed_in(store)
    ydl = FakeYdl(token_answer(make_access(), make_refresh()))
    auth.renew(tokens, ydl, store=store)
    [request] = ydl.requests
    assert request.url == "https://secure.soundcloud.com/oauth/token"
    assert request.method == "POST"
    assert form_of(request) == {
        "grant_type": "refresh_token",
        "client_id": tokens.client_id,
        "refresh_token": tokens.refresh,
    }


@pytest.mark.parametrize("refresh_back", ["new", "same", "none"])
def test_a_renewal_stores_the_pair_that_came_back(
    refresh_back: str, store: TokenStore, backend: FakeKeyring
) -> None:
    old = signed_in(store)
    new_access = make_access(NOW + 24 * HOUR, old.client_id)
    sent_refresh = {"new": make_refresh(), "same": old.refresh, "none": None}[refresh_back]
    new = auth.renew(old, FakeYdl(token_answer(new_access, sent_refresh)), store=store)
    expected_refresh = sent_refresh or old.refresh
    assert new == Tokens(new_access, expected_refresh, old.client_id)
    assert TokenStore(backend).load() == new


def test_a_renewal_points_yt_dlp_at_the_new_token(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    old = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {old.access}"
    new_access = make_access()
    auth.renew(old, FakeYdl(token_answer(new_access, make_refresh())), store=store)
    assert ytdlp_headers == {"Authorization": f"OAuth {new_access}"}


def test_the_header_changed_is_the_one_yt_dlps_soundcloud_extractor_sends(
    store: TokenStore,
) -> None:
    old = signed_in(store)
    new_access = make_access()
    auth.renew(old, FakeYdl(token_answer(new_access, make_refresh())), store=store)
    with engine.make_ydl() as ydl:
        extractor = ydl.get_info_extractor("Soundcloud")
    assert extractor._HEADERS["Authorization"] == f"OAuth {new_access}"


def test_invalid_grant_ends_the_sign_in_and_deletes_the_tokens(
    store: TokenStore, backend: FakeKeyring, ytdlp_headers: dict[str, str]
) -> None:
    old = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {old.access}"
    with pytest.raises(auth.SignInEnded) as error_info:
        auth.renew(old, FakeYdl(oauth_error("invalid_grant")), store=store)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert backend.entries == {}
    assert ytdlp_headers == {}


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (oauth_error("invalid_request"), ErrorKind.auth_expired),
        (oauth_error("invalid_client", status=401), ErrorKind.auth_expired),
        (status_error(503), ErrorKind.service_down),
        (status_error(429), ErrorKind.rate_limited),
        (TransportError(msg="Could not resolve host"), ErrorKind.offline),
    ],
    ids=["invalid_request", "invalid_client", "503", "429", "offline"],
)
def test_a_renewal_that_fails_another_way_keeps_the_tokens(
    error: Exception, kind: ErrorKind, store: TokenStore
) -> None:
    old = signed_in(store)
    with pytest.raises(ScmpError) as error_info:
        auth.renew(old, FakeYdl(error), store=store)
    assert not isinstance(error_info.value, auth.SignInEnded)
    assert error_info.value.kind is kind
    assert store.load() == old


def test_a_renewal_answer_without_an_access_token_means_the_engine_is_outdated(
    store: TokenStore,
) -> None:
    old = signed_in(store)
    answer = json.dumps({"refresh_token": make_refresh()}).encode()
    with pytest.raises(ScmpError) as error_info:
        auth.renew(old, FakeYdl(answer), store=store)
    assert error_info.value.kind is ErrorKind.engine_outdated
    assert store.load() == old


def test_renewing_after_a_sign_out_is_auth_expired(store: TokenStore) -> None:
    old = signed_in(store)
    auth.sign_out(store=store)
    ydl = FakeYdl()
    with pytest.raises(auth.SignInEnded) as error_info:
        auth.renew(old, ydl, store=store)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert ydl.requests == []


def test_a_sign_out_while_a_renewal_logs_leaves_yt_dlp_without_a_header(
    store: TokenStore,
    backend: FakeKeyring,
    ytdlp_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = signed_in(store)
    ydl = FakeYdl(token_answer(make_access(), make_refresh()))
    reached, go_on = threading.Event(), threading.Event()
    real_info = auth.log.info

    def held_info(*args: Any, **kwargs: Any) -> None:
        # Stands in for a thread switch while the renewal writes its log line.
        if threading.current_thread() is renewer:
            reached.set()
            go_on.wait(timeout=5)
        real_info(*args, **kwargs)

    monkeypatch.setattr(auth.log, "info", held_info)
    renewer = threading.Thread(target=auth.renew, args=(old, ydl), kwargs={"store": store})
    renewer.start()
    assert reached.wait(timeout=5)
    auth.sign_out(store=store)
    go_on.set()
    renewer.join(timeout=5)
    assert ytdlp_headers == {}
    assert backend.entries == {}


@pytest.fixture
def auth_lock_held(scmp3_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FileLock]:
    """auth.lock held through a second handle, as another sc-mp3 process renewing holds it."""
    monkeypatch.setattr(token_store, "AUTH_LOCK_TIMEOUT", 0)
    lock = FileLock(scmp3_home / "config" / token_store.AUTH_LOCK_FILE, 0)
    lock.acquire()
    yield lock
    lock.release()


def test_a_renewal_that_cant_get_the_lock_file_fails_like_the_network(
    store: TokenStore, auth_lock_held: FileLock
) -> None:
    old = signed_in(store)
    ydl = FakeYdl()
    with pytest.raises(ScmpError) as error_info:
        auth.renew(old, ydl, store=store)
    assert error_info.value.kind is ErrorKind.offline
    assert ydl.requests == []
    assert store.load() == old


def test_a_token_with_time_left_carries_on_while_another_process_renews(
    store: TokenStore, auth_lock_held: FileLock
) -> None:
    tokens = signed_in(store, exp=NOW + 300)
    assert auth.current_token(FakeYdl(), store=store, clock=clock) == tokens.access


def test_an_expired_token_waiting_for_another_process_is_offline(
    store: TokenStore, auth_lock_held: FileLock
) -> None:
    signed_in(store, exp=NOW - 1)
    with pytest.raises(ScmpError) as error_info:
        auth.current_token(FakeYdl(), store=store, clock=clock)
    assert error_info.value.kind is ErrorKind.offline


def test_a_sign_out_goes_ahead_when_another_process_keeps_the_lock(
    store: TokenStore,
    backend: FakeKeyring,
    ytdlp_headers: dict[str, str],
    auth_lock_held: FileLock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tokens = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {tokens.access}"
    auth.sign_out(store=store)
    assert backend.entries == {}
    assert ytdlp_headers == {}
    assert "kept auth.lock too long, so this goes ahead" in caplog.text
    for value in (tokens.access, tokens.refresh, tokens.client_id):
        assert value not in caplog.text


@pytest.mark.parametrize("seconds_left", [HOUR, 60], ids=["fresh token", "token about to expire"])
def test_a_sign_in_while_another_process_keeps_the_lock_is_offline_and_saves_nothing(
    seconds_left: int,
    store: TokenStore,
    ytdlp_headers: dict[str, str],
    auth_lock_held: FileLock,
) -> None:
    old = signed_in(store)
    new = make_tokens(exp=NOW + seconds_left)
    # No answers at all, since a busy lock must stop the sign-in before any request.
    ydl = FakeYdl()
    with pytest.raises(ScmpError) as error_info:
        auth.sign_in(new.access, new.refresh, ydl, store=store, clock=clock)
    assert error_info.value.kind is ErrorKind.offline
    assert ydl.requests == []
    assert store.load() == old
    assert ytdlp_headers == {}


@pytest.fixture
def file_locks_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every lock call fail as flock does on an NFS home with no lock service."""

    def refuse(handle: object) -> bool:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(filelock, "_try_lock", refuse)


def test_without_file_locks_a_renewal_still_runs_and_names_no_path(
    store: TokenStore,
    scmp3_home: Path,
    file_locks_refused: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    signed_in(store, exp=NOW + 300)
    new_access = make_access()
    ydl = FakeYdl(token_answer(new_access, make_refresh()))
    assert auth.current_token(ydl, store=store, clock=clock) == new_access
    assert "auth.lock can't be used" in caplog.text
    assert str(scmp3_home) not in caplog.text


def test_without_file_locks_signing_in_and_out_still_work(
    store: TokenStore,
    backend: FakeKeyring,
    ytdlp_headers: dict[str, str],
    file_locks_refused: None,
) -> None:
    tokens = make_tokens()
    auth.sign_in(tokens.access, tokens.refresh, FakeYdl(me_answer()), store=store, clock=clock)
    assert store.load() == tokens
    auth.sign_out(store=store)
    assert backend.entries == {}
    assert ytdlp_headers == {}


# A second sc-mp3 process: it takes auth.lock, waits for a line, then saves the given pair.
OTHER_PROCESS = """
import sys
from scmp3.core import paths, token_store
from scmp3.core.filelock import FileLock
from scmp3.core.token_store import TokenStore
from scmp3.core.tokens import Tokens

with FileLock(paths.config_dir() / token_store.AUTH_LOCK_FILE, 10):
    print("locked", flush=True)
    sys.stdin.readline()
    TokenStore().save(Tokens(*sys.argv[1:4]))
"""


@contextmanager
def another_process_saving(tokens: Tokens) -> Iterator[Callable[[], None]]:
    """A second sc-mp3 process that holds auth.lock until told to save `tokens` and let go."""
    # The child's keyring refuses every call, so it saves to the shared auth.json.
    child_env = {**os.environ, "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    other = subprocess.Popen(
        [sys.executable, "-c", OTHER_PROCESS, tokens.access, tokens.refresh, tokens.client_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=child_env,
    )

    def save_and_let_go() -> None:
        assert other.stdin is not None
        other.stdin.write("\n")
        other.stdin.flush()
        assert other.wait(timeout=20) == 0

    try:
        assert other.stdout is not None
        assert other.stdout.readline().strip() == "locked"
        yield save_and_let_go
    finally:
        if other.poll() is None:
            other.kill()


class OnItsOwnThread:
    """Runs a call on a thread of its own, and keeps what it returned or raised."""

    def __init__(self, call: Callable[[], object]) -> None:
        self.result: object = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(call,))
        self._thread.start()
        # Long enough for the call to reach auth.lock and wait there.
        threading.Event().wait(0.3)

    def _run(self, call: Callable[[], object]) -> None:
        try:
            self.result = call()
        except BaseException as error:
            self.error = error

    def finish(self) -> None:
        self._thread.join(timeout=20)
        assert not self._thread.is_alive()


def test_a_renewal_waits_for_another_process_and_takes_the_pair_it_stored(
    store: TokenStore,
) -> None:
    old = signed_in(store)
    newer = make_tokens()
    with another_process_saving(newer) as save_and_let_go:
        renewal = OnItsOwnThread(lambda: auth.renew(old, FakeYdl(), store=store))
        save_and_let_go()
    renewal.finish()
    assert renewal.error is None
    assert renewal.result == newer


def test_a_sign_out_waits_for_another_processs_renewal_and_leaves_nothing_behind(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    tokens = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {tokens.access}"
    with another_process_saving(make_tokens()) as save_and_let_go:
        signing_out = OnItsOwnThread(lambda: auth.sign_out(store=store))
        save_and_let_go()
    signing_out.finish()
    assert signing_out.error is None
    assert store.load() is None
    assert ytdlp_headers == {}


def test_a_sign_in_waits_for_another_processs_renewal_and_saves_over_it(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    signed_in(store)
    new = make_tokens()
    ydl = FakeYdl(me_answer())
    with another_process_saving(make_tokens()) as save_and_let_go:
        signing_in = OnItsOwnThread(
            lambda: auth.sign_in(new.access, new.refresh, ydl, store=store, clock=clock)
        )
        save_and_let_go()
    signing_in.finish()
    assert signing_in.error is None
    assert store.load() == new
    assert ytdlp_headers == {"Authorization": f"OAuth {new.access}"}


def test_a_job_that_renews_after_another_gets_the_stored_pair_without_a_call(
    store: TokenStore,
) -> None:
    old = signed_in(store)
    new = auth.renew(old, FakeYdl(token_answer(make_access(), make_refresh())), store=store)
    late = FakeYdl()
    assert auth.renew(old, late, store=store) == new
    assert late.requests == []


def test_a_renewal_another_process_already_made_points_yt_dlp_at_its_token(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    old = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {old.access}"
    # Another process renewed, so the store holds a newer pair than this process sends.
    newer = make_tokens()
    store.save(newer)
    late = FakeYdl()
    assert auth.renew(old, late, store=store) == newer
    assert late.requests == []
    assert ytdlp_headers == {"Authorization": f"OAuth {newer.access}"}


def test_a_renewal_that_finds_nobody_signed_in_clears_yt_dlps_header(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    old = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {old.access}"
    # Another process signed out while this one waited.
    store.delete()
    with pytest.raises(auth.SignInEnded):
        auth.renew(old, FakeYdl(), store=store)
    assert ytdlp_headers == {}


def test_two_threads_renewing_at_once_make_one_call(store: TokenStore) -> None:
    old = signed_in(store, exp=NOW + 60)
    new_access, new_refresh = make_access(), make_refresh()
    calls: list[Request] = []
    second_call = threading.Event()

    def answer(request: Request) -> bytes:
        calls.append(request)
        if len(calls) == 1:
            # Without the lock, the other thread would make its own call while this one waits.
            second_call.wait(timeout=0.5)
        else:
            second_call.set()
        return token_answer(new_access, new_refresh)

    ydl = FakeYdl(answer, answer)
    start = threading.Barrier(2)
    results: list[Tokens] = []

    def renew() -> None:
        start.wait()
        results.append(auth.renew(old, ydl, store=store))

    threads = [threading.Thread(target=renew) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(calls) == 1
    assert [tokens.access for tokens in results] == [new_access, new_access]


# The current token


def test_there_is_no_token_when_nobody_is_signed_in(store: TokenStore) -> None:
    ydl = FakeYdl()
    assert auth.current_token(ydl, store=store, clock=clock) is None
    assert ydl.requests == []


@pytest.mark.parametrize("seconds_left", [HOUR, auth.RENEW_MARGIN])
def test_a_token_with_time_left_is_used_as_is(seconds_left: int, store: TokenStore) -> None:
    tokens = signed_in(store, exp=NOW + seconds_left)
    ydl = FakeYdl()
    assert auth.current_token(ydl, store=store, clock=clock) == tokens.access
    assert ydl.requests == []


@pytest.mark.parametrize("seconds_left", [auth.RENEW_MARGIN - 1, 60, -HOUR])
def test_a_token_near_or_past_exp_is_renewed_first(seconds_left: int, store: TokenStore) -> None:
    signed_in(store, exp=NOW + seconds_left)
    new_access = make_access(NOW + 24 * HOUR)
    ydl = FakeYdl(token_answer(new_access, make_refresh()))
    assert auth.current_token(ydl, store=store, clock=clock) == new_access
    assert len(ydl.requests) == 1
    stored = store.load()
    assert stored is not None
    assert stored.access == new_access


def test_a_stored_token_that_isnt_a_jwt_is_renewed(store: TokenStore) -> None:
    store.save(Tokens("not-a-jwt-at-all", make_refresh(), claim_client_id()))
    new_access = make_access()
    ydl = FakeYdl(token_answer(new_access))
    assert auth.current_token(ydl, store=store, clock=clock) == new_access


def test_a_failed_early_renewal_still_gives_the_token_while_it_works(
    store: TokenStore, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = signed_in(store, exp=NOW + 300)
    ydl = FakeYdl(status_error(503))
    assert auth.current_token(ydl, store=store, clock=clock) == tokens.access
    assert "Couldn't renew the SoundCloud sign-in early" in caplog.text


def test_a_failed_renewal_of_an_expired_token_raises(store: TokenStore) -> None:
    signed_in(store, exp=NOW - 1)
    with pytest.raises(ScmpError) as error_info:
        auth.current_token(FakeYdl(status_error(503)), store=store, clock=clock)
    assert error_info.value.kind is ErrorKind.service_down


def test_an_ended_sign_in_raises_even_with_time_left(store: TokenStore) -> None:
    signed_in(store, exp=NOW + 300)
    with pytest.raises(auth.SignInEnded):
        auth.current_token(FakeYdl(oauth_error("invalid_grant")), store=store, clock=clock)
    assert store.load() is None


def test_a_refused_early_renewal_carries_on_and_logs_the_error_code(
    store: TokenStore, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = signed_in(store, exp=NOW + 300)
    ydl = FakeYdl(oauth_error("invalid_client", status=401))
    assert auth.current_token(ydl, store=store, clock=clock) == tokens.access
    assert "error code invalid_client" in caplog.text
    assert store.load() == tokens


def test_a_refused_renewal_of_an_expired_token_is_auth_expired_and_keeps_the_tokens(
    store: TokenStore,
) -> None:
    tokens = signed_in(store, exp=NOW - 1)
    with pytest.raises(ScmpError) as error_info:
        auth.current_token(FakeYdl(oauth_error("invalid_request")), store=store, clock=clock)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert not isinstance(error_info.value, auth.SignInEnded)
    assert store.load() == tokens


def test_a_renewal_that_outlasts_the_token_doesnt_count_as_still_working(
    store: TokenStore,
) -> None:
    signed_in(store, exp=NOW + 20)
    now = [NOW]

    def slow_failure(request: Request) -> bytes:
        # The socket timeout passes while the network is down.
        now[0] += 30
        raise TransportError(msg="timed out")

    with pytest.raises(ScmpError) as error_info:
        auth.current_token(FakeYdl(slow_failure), store=store, clock=lambda: now[0])
    assert error_info.value.kind is ErrorKind.offline


def test_current_token_points_yt_dlp_at_the_token_it_gives(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    tokens = signed_in(store)
    # As left by an older token, before another process renewed.
    ytdlp_headers["Authorization"] = "OAuth older-token"
    assert auth.current_token(FakeYdl(), store=store, clock=clock) == tokens.access
    assert ytdlp_headers == {"Authorization": f"OAuth {tokens.access}"}


def test_current_token_clears_yt_dlps_header_when_nobody_is_signed_in(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    # As left behind when another process signed out.
    ytdlp_headers["Authorization"] = "OAuth signed-out-token"
    assert auth.current_token(FakeYdl(), store=store, clock=clock) is None
    assert ytdlp_headers == {}


def test_a_sign_out_while_current_token_holds_a_loaded_pair_leaves_no_header(
    store: TokenStore,
    backend: FakeKeyring,
    ytdlp_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(store)
    loaded, go_on = threading.Event(), threading.Event()
    real_load = store.load

    def held_load() -> Tokens | None:
        tokens = real_load()
        # Stands in for a thread switch between the load and the header write.
        if threading.current_thread() is job and not loaded.is_set():
            loaded.set()
            go_on.wait(timeout=5)
        return tokens

    monkeypatch.setattr(store, "load", held_load)
    job = threading.Thread(
        target=auth.current_token, args=(FakeYdl(),), kwargs={"store": store, "clock": clock}
    )
    job.start()
    assert loaded.wait(timeout=5)
    signing_out = OnItsOwnThread(lambda: auth.sign_out(store=store))
    go_on.set()
    job.join(timeout=5)
    signing_out.finish()
    assert signing_out.error is None
    assert ytdlp_headers == {}
    assert backend.entries == {}


def test_an_access_token_too_big_for_credential_manager_is_renewed_once_not_every_job() -> None:
    backend = CredentialManagerKeyring()
    store = TokenStore(backend)
    signed_in(store, exp=NOW + 60)
    big = make_access(NOW + 24 * HOUR, length=1300)
    first_job = FakeYdl(token_answer(big, make_refresh()))
    assert auth.current_token(first_job, store=store, clock=clock) == big
    for _ in range(3):
        # Each job in a run of its own, as with the CLI, so only the saved_at stamps decide.
        TokenStore.try_keyring_again()
        later_job = FakeYdl()
        assert auth.current_token(later_job, store=TokenStore(backend), clock=clock) == big
        assert later_job.requests == []


class SwitchableKeyring(FakeKeyring):
    """Refuses every call with KeyringLocked while `locked` is set."""

    locked = False

    def get_password(self, service: str, username: str) -> str | None:
        self._refuse_when_locked()
        return super().get_password(service, username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._refuse_when_locked()
        super().set_password(service, username, password)

    def _refuse_when_locked(self) -> None:
        if self.locked:
            raise KeyringLocked("locked")


def test_a_locked_keyring_isnt_asked_on_every_job() -> None:
    locked = CountingKeyring(BrokenKeyring(KeyringLocked("locked")))
    store = TokenStore(locked)
    tokens = make_tokens()
    auth.sign_in(tokens.access, tokens.refresh, FakeYdl(me_answer()), store=store, clock=clock)
    asked = locked.calls
    for _ in range(10):
        assert auth.current_token(FakeYdl(), store=store, clock=clock) == tokens.access
    assert locked.calls == asked


def test_one_failed_keyring_read_doesnt_hide_a_keyring_sign_in_from_later_jobs(
    ytdlp_headers: dict[str, str],
) -> None:
    keyring_backend = SwitchableKeyring()
    store = TokenStore(keyring_backend)
    tokens = signed_in(store)
    # One read fails, as a D-Bus timeout or a dismissed prompt would.
    keyring_backend.locked = True
    assert auth.current_token(FakeYdl(), store=store, clock=clock) is None
    keyring_backend.locked = False
    for _ in range(2):
        assert auth.current_token(FakeYdl(), store=store, clock=clock) == tokens.access
    assert ytdlp_headers == {"Authorization": f"OAuth {tokens.access}"}


def test_a_sign_in_tries_the_keyring_again_after_it_refused() -> None:
    keyring_backend = SwitchableKeyring()
    keyring_backend.locked = True
    TokenStore(keyring_backend).load()
    keyring_backend.locked = False
    tokens = make_tokens()
    store = TokenStore(keyring_backend)
    auth.sign_in(tokens.access, tokens.refresh, FakeYdl(me_answer()), store=store, clock=clock)
    assert store.location == "keyring"
    assert len(keyring_backend.entries) == 2


def test_a_sign_out_lets_the_next_save_try_the_keyring_again() -> None:
    keyring_backend = SwitchableKeyring()
    keyring_backend.locked = True
    TokenStore(keyring_backend).load()
    keyring_backend.locked = False
    auth.sign_out(store=TokenStore(keyring_backend))
    store = TokenStore(keyring_backend)
    store.save(make_tokens())
    assert store.location == "keyring"


# Checking the account


@pytest.mark.parametrize(
    ("product_id", "tier"),
    [
        ("free", "free"),
        ("consumer-free", "free"),
        ("consumer-mid-tier", "go"),
        ("consumer-high-tier", "go_plus"),
        ("consumer-high-dj-tier", "dj"),
    ],
)
def test_validate_reads_the_account_and_its_plan(product_id: str, tier: str) -> None:
    account = auth.validate(make_access(), FakeYdl(me_answer(product_id)))
    assert account == Account(
        username="Kaia",
        permalink="kaia-music",
        avatar_url="https://i1.sndcdn.com/avatars-000-abc-large.jpg",
        tier=tier,
    )


def test_validate_sends_the_token_to_me() -> None:
    token = make_access()
    ydl = FakeYdl(me_answer())
    auth.validate(token, ydl)
    [request] = ydl.requests
    assert request.url == "https://api-v2.soundcloud.com/me"
    assert request.headers["Authorization"] == f"OAuth {token}"


@pytest.mark.parametrize(
    "fields",
    [
        {"consumer_subscription": {"product": {"id": "consumer-new-tier"}}},
        {"consumer_subscription": None},
        {"consumer_subscription": {"product": None}},
    ],
    ids=["new id", "no subscription", "no product"],
)
def test_an_unknown_plan_counts_as_free_and_is_logged(
    fields: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    account = auth.validate(make_access(), FakeYdl(me_answer(**fields)))
    assert account.tier == "free"
    assert "Unknown SoundCloud product id" in caplog.text


def test_an_account_without_a_picture_has_none() -> None:
    account = auth.validate(make_access(), FakeYdl(me_answer(avatar_url=None)))
    assert account.avatar_url is None


def test_a_401_from_me_means_the_sign_in_expired() -> None:
    ydl = FakeYdl(status_error(401, auth.ME_URL))
    with pytest.raises(ScmpError) as error_info:
        auth.validate(make_access(), ydl)
    assert error_info.value.kind is ErrorKind.auth_expired


@pytest.mark.parametrize(
    "fields", [{"username": None}, {"permalink": ""}, {"username": 5}], ids=["none", "empty", "5"]
)
def test_me_without_the_account_name_means_the_engine_is_outdated(fields: dict[str, Any]) -> None:
    with pytest.raises(ScmpError) as error_info:
        auth.validate(make_access(), FakeYdl(me_answer(**fields)))
    assert error_info.value.kind is ErrorKind.engine_outdated


def test_the_me_answer_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    auth.validate(make_access(), FakeYdl(me_answer("consumer-new-tier")))
    for private in ("kaia@example.com", "Kaia Example", "1999"):
        assert private not in caplog.text


# Signing in and out


def test_sign_in_checks_stores_and_points_yt_dlp_at_the_token(
    store: TokenStore, ytdlp_headers: dict[str, str]
) -> None:
    tokens = make_tokens()
    ydl = FakeYdl(me_answer("consumer-high-tier"))
    access, refresh = f"OAuth {tokens.access}", f' "{tokens.refresh}" '
    account = auth.sign_in(access, refresh, ydl, store=store, clock=clock)
    assert account.tier == "go_plus"
    assert store.load() == tokens
    assert ytdlp_headers == {"Authorization": f"OAuth {tokens.access}"}
    assert [request.url for request in ydl.requests] == [auth.ME_URL]


def test_sign_in_refuses_swapped_values_before_any_request(store: TokenStore) -> None:
    tokens = make_tokens()
    ydl = FakeYdl()
    with pytest.raises(TokenRefused):
        auth.sign_in(tokens.refresh, tokens.access, ydl, store=store, clock=clock)
    assert ydl.requests == []
    assert store.load() is None


def test_sign_in_refuses_an_access_value_that_isnt_a_token_before_any_request(
    store: TokenStore,
) -> None:
    ydl = FakeYdl()
    with pytest.raises(TokenRefused) as error_info:
        auth.sign_in(OLD_SHAPE, make_refresh(), ydl, store=store, clock=clock)
    assert error_info.value.problem is TokenProblem.access_not_token
    assert ydl.requests == []
    assert store.load() is None


def test_sign_in_doesnt_store_a_token_me_refuses(store: TokenStore) -> None:
    tokens = make_tokens()
    ydl = FakeYdl(status_error(401, auth.ME_URL))
    with pytest.raises(ScmpError) as error_info:
        auth.sign_in(tokens.access, tokens.refresh, ydl, store=store, clock=clock)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert store.load() is None


def test_sign_in_renews_an_access_token_about_to_expire_first(store: TokenStore) -> None:
    old = make_tokens(exp=NOW - HOUR)
    new_access, new_refresh = make_access(NOW + 24 * HOUR, old.client_id), make_refresh()
    ydl = FakeYdl(token_answer(new_access, new_refresh), me_answer())
    auth.sign_in(old.access, old.refresh, ydl, store=store, clock=clock)
    renewal, me = ydl.requests
    assert form_of(renewal)["refresh_token"] == old.refresh
    assert me.headers["Authorization"] == f"OAuth {new_access}"
    assert store.load() == Tokens(new_access, new_refresh, old.client_id)


def test_sign_in_keeps_a_renewed_pair_even_when_me_fails(store: TokenStore) -> None:
    old = make_tokens(exp=NOW - HOUR)
    new_access, new_refresh = make_access(), make_refresh()
    ydl = FakeYdl(token_answer(new_access, new_refresh), status_error(503, auth.ME_URL))
    with pytest.raises(ScmpError):
        auth.sign_in(old.access, old.refresh, ydl, store=store, clock=clock)
    assert store.load() == Tokens(new_access, new_refresh, old.client_id)


def test_sign_out_deletes_both_entries_and_clears_yt_dlps_header(
    store: TokenStore, backend: FakeKeyring, ytdlp_headers: dict[str, str]
) -> None:
    tokens = signed_in(store)
    ytdlp_headers["Authorization"] = f"OAuth {tokens.access}"
    auth.sign_out(store=store)
    assert backend.entries == {}
    assert ytdlp_headers == {}


def test_sign_out_clears_the_header_even_when_the_keyring_cant_delete(
    ytdlp_headers: dict[str, str],
) -> None:
    ytdlp_headers["Authorization"] = "OAuth x"
    store = TokenStore(BrokenKeyring(CredError(5, "CredDelete", "denied")))
    with pytest.raises(ScmpError):
        auth.sign_out(store=store)
    assert ytdlp_headers == {}


# Keeping tokens secret


@pytest.mark.parametrize(
    ("read", "secret_values"),
    [
        (
            lambda tokens: auth.validate(tokens.access, FakeYdl(me_answer())),
            lambda tokens: {tokens.access},
        ),
    ],
    ids=["validate"],
)
def test_a_token_is_registered_as_a_secret_as_soon_as_it_is_read(
    read: Callable[[Tokens], object],
    secret_values: Callable[[Tokens], set[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = make_tokens()
    registered: list[str] = []
    monkeypatch.setattr(auth, "register_secret", registered.append)
    read(tokens)
    assert set(registered) == secret_values(tokens)


def test_a_renewal_registers_both_new_tokens(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = signed_in(store)
    new_access, new_refresh = make_access(), make_refresh()
    registered: list[str] = []
    monkeypatch.setattr(auth, "register_secret", registered.append)
    auth.renew(old, FakeYdl(token_answer(new_access, new_refresh)), store=store)
    assert {new_access, new_refresh} <= set(registered)


def full_text(error: BaseException) -> str:
    """An exception's message and traceback, with every exception chained to it."""
    return "".join(traceback.format_exception(error))


OLD_SHAPE = "2-294712-12345678-AbCdEf12"


def test_no_token_reaches_the_log_or_exception_text(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    tokens = make_tokens()
    errors: list[BaseException] = []

    def expect_error(action: Callable[[], object]) -> None:
        with pytest.raises(Exception) as error_info:
            action()
        errors.append(error_info.value)

    # A keyring whose error messages quote both tokens, so only redaction keeps them out.
    store = TokenStore(BrokenKeyring(CredError(f"refused {tokens.access} and {tokens.refresh}")))
    auth.sign_in(tokens.access, tokens.refresh, FakeYdl(me_answer()), store=store, clock=clock)
    # Five minutes before exp, so it renews, fails with a 503 and carries on with the old token.
    five_minutes_left = lambda: NOW + HOUR - 300  # noqa: E731
    auth.current_token(FakeYdl(status_error(503)), store=store, clock=five_minutes_left)
    expect_error(lambda: auth.renew(tokens, FakeYdl(oauth_error("invalid_client")), store=store))
    expect_error(lambda: auth.renew(tokens, FakeYdl(oauth_error("invalid_grant")), store=store))
    expect_error(lambda: auth.validate(tokens.access, FakeYdl(status_error(401, auth.ME_URL))))
    expect_error(lambda: read_claims(tokens.refresh))
    expect_error(lambda: read_claims(OLD_SHAPE))
    expect_error(lambda: normalize_access(tokens.refresh))
    expect_error(lambda: normalize_refresh(tokens.access))
    expect_error(lambda: auth.sign_out(store=store))

    def full_disk(path: Path, text: str) -> None:
        raise ScmpError(ErrorKind.disk, "can't write auth.json: There is not enough space")

    # The file fallback fails too, so its error must not carry the keyring's text as context.
    with monkeypatch.context() as patch:
        patch.setattr(paths, "write_text_atomically", full_disk)
        expect_error(lambda: store.save(tokens))

    assert len(errors) == 9
    assert "Couldn't renew the SoundCloud sign-in early" in caplog.text
    assert "SoundCloud ended the sign-in" in caplog.text
    for value in (tokens.access, tokens.refresh, tokens.client_id, OLD_SHAPE):
        assert value not in caplog.text
        for error in errors:
            assert value not in full_text(error)
