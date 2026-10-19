"""Tests for the token-refresh flow, with fake token servers and no network."""

import base64
import json
from urllib.parse import parse_qs

from refresh_check import (
    RefreshTransportError,
    compare_client,
    refresh_once,
    run_refresh_test,
)

IAT = 1_700_000_000


def base64url_json(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict[str, object]) -> str:
    header = {"alg": "RS256", "typ": "at+JWT"}
    return f"{base64url_json(header)}.{base64url_json(claims)}.c2ln"


def token_reply(refresh_token: str) -> bytes:
    access = make_jwt({"iat": IAT, "exp": IAT + 3600, "client_id": "web"})
    reply = {
        "access_token": access,
        "refresh_token": refresh_token,
        "expires_in": 3600,
        "scope": "*",
        "token_type": "bearer",
    }
    return json.dumps(reply).encode()


def sent_refresh_token(body: bytes) -> str:
    return parse_qs(body.decode())["refresh_token"][0]


class RotatingServer:
    """Each refresh token works once and is replaced by a new one."""

    def __init__(self) -> None:
        self.valid = {"refresh-1"}
        self.issued = 1
        self.seen: list[str] = []

    def send(self, body: bytes) -> tuple[int, bytes]:
        refresh_token = sent_refresh_token(body)
        self.seen.append(refresh_token)
        if refresh_token not in self.valid:
            return 401, json.dumps({"error": "invalid_grant"}).encode()
        self.valid.discard(refresh_token)
        self.issued += 1
        new_refresh = f"refresh-{self.issued}"
        self.valid.add(new_refresh)
        return 200, token_reply(new_refresh)


class ReuseDetectingServer(RotatingServer):
    """Like RotatingServer, but a reused token revokes the whole family (RFC 9700 4.14.2)."""

    def __init__(self) -> None:
        super().__init__()
        self.used: set[str] = set()

    def send(self, body: bytes) -> tuple[int, bytes]:
        refresh_token = sent_refresh_token(body)
        if refresh_token in self.used:
            self.seen.append(refresh_token)
            self.valid.clear()
            return 400, json.dumps({"error": "invalid_grant"}).encode()
        self.used.add(refresh_token)
        return super().send(body)


class NonRotatingServer:
    """Hands back the same refresh token it was sent."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def send(self, body: bytes) -> tuple[int, bytes]:
        refresh_token = sent_refresh_token(body)
        self.seen.append(refresh_token)
        return 200, token_reply(refresh_token)


def test_refresh_once_records_fields_and_lifetime_but_no_token_value() -> None:
    call, tokens = refresh_once("renew", "web", "refresh-1", RotatingServer().send)

    assert call.status == 200
    assert call.field_names == [
        "access_token",
        "expires_in",
        "refresh_token",
        "scope",
        "token_type",
    ]
    assert call.token_type == "bearer"
    assert call.expires_in == 3600
    assert call.access_token_returned and call.refresh_token_returned
    assert call.access_lifetime[0] == "exp - iat (full lifetime): 3600 s, 1.00 hours"
    assert tokens.refresh == "refresh-2"
    assert "refresh-2" not in repr(call)


def test_refresh_once_records_that_a_rotated_token_differs() -> None:
    call, _ = refresh_once("renew", "web", "refresh-1", RotatingServer().send)

    assert call.refresh_token_changed is True


def test_refresh_once_records_that_a_returned_token_is_the_same_one() -> None:
    call, _ = refresh_once("renew", "web", "refresh-1", NonRotatingServer().send)

    # The field came back, but the value is the one sent, so it isn't a new token.
    assert call.refresh_token_returned is True
    assert call.refresh_token_changed is False


def test_run_refresh_test_renews_twice_then_reuses_the_original_last() -> None:
    server = RotatingServer()

    calls, secrets = run_refresh_test("web", "refresh-1", server.send)

    assert server.seen == ["refresh-1", "refresh-2", "refresh-1"]
    assert [call.status for call in calls] == [200, 200, 401]
    assert {"refresh-1", "refresh-2", "refresh-3"} <= set(secrets)


def test_reuse_detection_doesnt_make_the_returned_token_look_broken() -> None:
    server = ReuseDetectingServer()

    calls, _ = run_refresh_test("web", "refresh-1", server.send)

    # The returned token is tried before the reuse, so only the reuse call fails.
    assert [call.status for call in calls] == [200, 200, 400]


def test_run_refresh_test_skips_the_reuse_check_when_the_token_didnt_change() -> None:
    server = NonRotatingServer()

    calls, _ = run_refresh_test("web", "refresh-1", server.send)

    assert len(calls) == 2
    assert calls[0].refresh_token_changed is False


def test_run_refresh_test_stops_when_the_first_renewal_fails() -> None:
    calls, _ = run_refresh_test("web", "wrong-token", RotatingServer().send)

    assert len(calls) == 1
    assert calls[0].status == 401
    assert calls[0].refresh_token_returned is False
    assert calls[0].refresh_token_changed is None


def test_run_refresh_test_respects_the_call_budget() -> None:
    server = RotatingServer()

    calls, _ = run_refresh_test("web", "refresh-1", server.send, max_calls=2)

    assert len(calls) == 2
    assert server.seen == ["refresh-1", "refresh-2"]


def test_run_refresh_test_labels_say_where_the_token_came_from() -> None:
    calls, _ = run_refresh_test("web", "refresh-1", RotatingServer().send, source="from the window")

    assert calls[0].label == "renew with the refresh token from the window"


def test_refresh_once_records_the_oauth_error_code_on_failure() -> None:
    def rejecting_send(_body: bytes) -> tuple[int, bytes]:
        return 400, json.dumps({"error": "invalid_grant", "error_description": "bad"}).encode()

    call, _ = refresh_once("renew", "web", "refresh-1", rejecting_send)

    assert call.status == 400
    assert call.error_fields == {"error": "invalid_grant", "error_description": "bad"}


def test_refresh_once_reports_a_transport_error_without_a_token() -> None:
    def failing_send(_body: bytes) -> tuple[int, bytes]:
        raise RefreshTransportError("request failed: TransportError")

    call, tokens = refresh_once("renew", "web", "refresh-1", failing_send)

    assert call.status is None
    assert call.error == "request failed: TransportError"
    assert tokens.refresh is None


def access_token(client_id: str) -> str:
    return make_jwt({"client_id": client_id, "iat": IAT, "exp": IAT + 3600})


def test_compare_client_says_same_and_returns_the_claim_for_the_request() -> None:
    verdict, claim = compare_client(access_token("web-id"), "web-id")

    assert verdict == "same"
    assert claim == "web-id"


def test_compare_client_says_different_when_the_claim_differs() -> None:
    verdict, claim = compare_client(access_token("other-id"), "web-id")

    assert verdict == "different"
    assert claim == "other-id"


def test_compare_client_says_unknown_without_a_client_id_claim() -> None:
    no_claim = f"{base64url_json({'alg': 'RS256', 'typ': 'at+JWT'})}.{base64url_json({})}.c2ln"

    assert compare_client(no_claim, "web-id") == ("unknown", None)
