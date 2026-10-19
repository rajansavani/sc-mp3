"""Prints the spike's findings. Every line goes through redaction, so no token can leak."""

import json
import os
import sys
import time
from dataclasses import asdict

from account_checks import (
    FAKE_TOKEN,
    MeResponse,
    SigninCheck,
    check_ytdlp_signin,
    fetch_me,
    scrape_client_id,
)
from login_window import SigninOutcome
from probe import ProbeStep
from redact import describe_token, redact_text
from refresh_check import RefreshCall
from token_info import lifetime_lines, read_jwt


class SafePrinter:
    """Prints lines after masking every registered secret, so a mistake elsewhere can't leak one."""

    def __init__(self) -> None:
        self.secrets: list[str] = []

    def line(self, text: str = "") -> None:
        print(redact_text(text, self.secrets), flush=True)

    def heading(self, text: str) -> None:
        self.line()
        self.line(f"== {text}")


def print_probe_steps(out: SafePrinter, steps: list[ProbeStep]) -> None:
    for step in steps:
        out.heading(step.label)
        if step.error:
            out.line(f"check failed: {step.error}")
            continue
        out.line(f"target found: {step.found_target}")
        if step.form_before:
            out.line(f"sign-in iframe before: {step.form_before}")
        out.line(f"page URL after: {step.url_after}")
        out.line(f"sign-in iframe after: {step.form_after}")
        if step.frame_url_after:
            out.line(f"sign-in iframe location after: {step.frame_url_after}")
            out.line(f"sign-in form shows: {step.form_messages}")
        out.line(f"links handed to the system browser: {step.browser_opens or 'none'}")
        if step.screenshot:
            out.line(f"screenshot: {step.screenshot}")
        out.line("sign-in flow requests:")
        for request in dict.fromkeys(step.flow_requests):
            out.line(f"  {request}")


def print_signin(out: SafePrinter, outcome: SigninOutcome) -> None:
    out.heading("Sign-in window")
    out.line(f"stopped because: {outcome.stop_reason}")
    out.line(f"form seen at: {seconds(outcome.form_seen_at)}")
    out.line(f"token seen at: {seconds(outcome.token_seen_at)}")
    if outcome.token:
        out.line(f"token seen by the {outcome.token_seen_by} check")
        out.line(f"token: {describe_token(outcome.token)}")
        print_token_facts(out, outcome.token)
    slowest = f"{outcome.slowest_poll * 1000:.0f} ms at {outcome.slowest_poll_at:.0f} s"
    out.line(f"cookie checks: {outcome.polls}, slowest {slowest}")
    out.line(f"cookie check errors: {outcome.poll_errors or 'none'}")
    out.line(f"links handed to the system browser: {outcome.browser_opens or 'none'}")
    if outcome.token_cookie:
        out.line(f"oauth_token cookie: {asdict(outcome.token_cookie)}")
    if outcome.refresh_cookie:
        facts = asdict(outcome.refresh_cookie)
        facts["length"] = len(outcome.refresh_token or "")
        out.line(f"oauth_refresh_token cookie: {facts}")
    else:
        out.line("oauth_refresh_token cookie: not present when the token appeared")
    out.heading("Failed /sign-in responses")
    for failure in outcome.signin_failures or ["none"]:
        out.line(f"  {failure}")
    out.heading("What the page and the sign-in form showed, each time it changed")
    for message in outcome.form_messages:
        out.line(f"  {message}")
    out.heading("Cookies in the window when the token appeared (no values)")
    for cookie in outcome.cookies_at_token:
        out.line(f"  {cookie.name} domain={cookie.domain} httponly={cookie.httponly}")
    out.heading("Timeline")
    for entry in outcome.timeline:
        out.line(f"  {entry}")


def print_token_facts(out: SafePrinter, token: str) -> None:
    """Print the JWT's algorithm, claim names and times, decoded locally."""
    out.heading("Token, decoded locally")
    facts = read_jwt(token)
    if facts is None:
        out.line("not a JWT")
        return
    out.line(f"header: alg={facts.alg}, typ={facts.typ}, keys {facts.header_keys}")
    out.line(f"payload claim names: {facts.claim_names}")
    for text in lifetime_lines(facts, int(time.time())):
        out.line(text)


def print_me_checks(out: SafePrinter, token: str) -> bool:
    """Print every /me attempt and return whether any of them accepted the token."""
    out.heading("GET /me through yt-dlp's opener")
    attempts = [fetch_me("no token", None), fetch_me("made-up token", FAKE_TOKEN)]
    real = fetch_me("real token", token)
    attempts.append(real)
    # Retry with what SoundCloud's web app also sends, to learn which parts are needed.
    if real.status != 200:
        client_id = scrape_client_id()
        attempts.append(fetch_me("token, client_id", token, client_id=client_id))
        attempts.append(
            fetch_me("token, client_id, impersonate", token, client_id=client_id, impersonate=True)
        )
    for attempt in attempts:
        print_me_response(out, attempt)
    return any(attempt.status == 200 for attempt in attempts[2:])


def print_me_response(out: SafePrinter, response: MeResponse) -> None:
    out.line(f"- {response.label}: status {response.status} {response.error}".rstrip())
    if response.shape is None:
        return
    out.line(f"  tiers: {response.tiers}")
    out.line("  keys, with values replaced by their type:")
    for text in json.dumps(response.shape, indent=2).splitlines():
        out.line(f"    {text}")


def print_ytdlp_checks(out: SafePrinter, token: str) -> None:
    out.heading("yt-dlp sign-in with username 'oauth'")
    # The order matters: yt-dlp keeps the header on the class once a token works.
    checks = [
        check_ytdlp_signin("guest, no credentials", None, extract=True),
        check_ytdlp_signin("made-up token first", FAKE_TOKEN, extract=False),
        check_ytdlp_signin("real token", token, extract=True),
        check_ytdlp_signin("made-up token after the real one", FAKE_TOKEN, extract=False),
    ]
    for check in checks:
        print_signin_check(out, check)


def print_signin_check(out: SafePrinter, check: SigninCheck) -> None:
    out.line(f"- {check.label}")
    out.line(f"  holds an Authorization header: {check.holds_auth_header}")
    out.line(f"  'Continuing as guest' warning: {check.guest_warning}")
    for url, format_ids in check.format_ids.items():
        out.line(f"  formats for {url}: {', '.join(format_ids) or 'none'}")
    for message in check.messages:
        out.line(f"    {message}")


def print_refresh_call(out: SafePrinter, call: RefreshCall) -> None:
    out.line(f"- {call.label}: status {call.status} {call.error}".rstrip())
    if not call.field_names:
        return
    out.line(f"  response fields: {call.field_names}")
    if call.error_fields:
        # Not labelled "OAuth error": the redactor masks the word after "OAuth".
        out.line(f"  error code: {call.error_fields}")
    out.line(f"  token_type: {call.token_type}, scope present: {call.scope_present}")
    out.line(f"  expires_in: {call.expires_in}")
    out.line(f"  access_token field returned: {call.access_token_returned}")
    out.line(f"  refresh_token field returned: {call.refresh_token_returned}")
    if call.refresh_token_changed is not None:
        out.line(
            f"  returned refresh token differs from the one sent: {call.refresh_token_changed}"
        )
    if call.access_token_header:
        out.line(f"  new access token header: {call.access_token_header}")
    for text in call.access_lifetime:
        out.line(f"  {text}")


def private_folder_removed() -> str:
    """Report whether pywebview deleted the WebView2 data folder it used for private mode."""
    winforms = sys.modules.get("webview.platforms.winforms")
    folder = getattr(winforms, "cache_dir", None)
    if not folder:
        return "not checked (not the WinForms backend)"
    return "no, it's still there" if os.path.exists(folder) else "yes"


def seconds(value: float | None) -> str:
    return "never" if value is None else f"{value:.2f} s after the window opened"
