"""Spike: SoundCloud sign-in in a pywebview window. See README.md in this folder."""

import argparse
import sys
import traceback
from pathlib import Path

from account_checks import fetch_me, forget_ytdlp_signin, scrape_client_id
from login_window import run_signin_window
from probe import run_probe
from redact import describe_token
from refresh_check import compare_client, run_refresh_test
from report import (
    SafePrinter,
    print_me_checks,
    print_me_response,
    print_probe_steps,
    print_refresh_call,
    print_signin,
    print_token_facts,
    print_ytdlp_checks,
    private_folder_removed,
)
from token_box import ask_for_token, reject_access_token, reject_non_access_token
from token_info import access_token_client_id

DEFAULT_TIMEOUT_SECONDS = 600.0
REFRESH_BOX_TITLE = "sc-mp3 refresh test"
REFRESH_BOX_INSTRUCTIONS = (
    "Paste the value of the oauth_refresh_token cookie from soundcloud.com, then select OK.\n"
    "It's the row just above oauth_token. It stays in memory. Nothing is saved."
)
ACCESS_BOX_INSTRUCTIONS = (
    "Paste the value of the oauth_token cookie from soundcloud.com, then select OK.\n"
    "It's read on this computer only, for its client_id. It stays in memory. Nothing is saved."
)


def main() -> int:
    args = parse_args()
    out = SafePrinter()
    if args.command == "login":
        return run_login(out, args.timeout, args.max_calls)
    if args.command == "probe":
        out.line("Running the automated checks in a private window.")
        print_probe_steps(out, run_probe(args.shots))
        return 0
    if args.command == "paste":
        return run_paste(out, args.timeout, args.lifetime_only)
    if args.command == "refresh" and args.compare_client:
        return run_compare_client(out, args.timeout)
    if args.command == "refresh":
        return run_refresh(out, args.timeout, args.max_calls)
    return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="open the sign-in window and wait for a person")
    login.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    add_max_calls(login)
    probe = commands.add_parser("probe", help="automated checks that need no account (Windows)")
    probe.add_argument(
        "--shots", type=Path, help="folder for screenshots; keep it outside the repo"
    )
    paste = commands.add_parser("paste", help="paste a token into a local box, then check it")
    paste.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    paste.add_argument(
        "--lifetime-only",
        action="store_true",
        help="only read the token's JWT times locally and make one /me call",
    )
    refresh = commands.add_parser(
        "refresh", help="paste the access and refresh tokens, then renew with them"
    )
    refresh.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    add_max_calls(refresh)
    refresh.add_argument(
        "--compare-client",
        action="store_true",
        help="compare the token's client_id with the web one, and renew once only if it differs",
    )
    return parser.parse_args()


def add_max_calls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-calls", type=int, default=3, choices=(1, 2, 3), help="renewal calls to allow"
    )


def run_login(out: SafePrinter, timeout: float, max_calls: int) -> int:
    out.line(f"Opening the sign-in window. Waiting up to {timeout:.0f} s for a sign-in.")
    outcome = run_signin_window(timeout)
    token, refresh_token = outcome.token, outcome.refresh_token
    # Register both values before anything prints. A refresh token can only be masked by value.
    out.secrets.extend(value for value in (token, refresh_token) if value)
    print_signin(out, outcome)
    outcome.token = outcome.refresh_token = None
    out.line(f"WebView2 private data folder removed after close: {private_folder_removed()}")
    if not token:
        return 1
    run_token_checks(out, token)
    if refresh_token:
        renew_with_claim(out, token, refresh_token, max_calls, source="from the window")
    return 0


def run_paste(out: SafePrinter, timeout: float, lifetime_only: bool) -> int:
    out.line(f"Opening the token box. Waiting up to {timeout:.0f} s.")
    token = ask_for_token(timeout)
    if not token:
        out.line("No token was entered.")
        return 1
    out.secrets.append(token)
    out.line(f"token: {describe_token(token)}")
    print_token_facts(out, token)
    if lifetime_only:
        # One /me call confirms the token still works; nothing else is sent anywhere.
        print_me_response(out, fetch_me("real token", token))
        return 0
    run_token_checks(out, token)
    return 0


def run_refresh(out: SafePrinter, timeout: float, max_calls: int) -> int:
    """Renew with the refresh token, using the client_id named inside the access token."""
    tokens = ask_for_both_tokens(out, timeout)
    if tokens is None:
        return 1
    access_token, refresh_token = tokens
    renew_with_claim(out, access_token, refresh_token, max_calls, source="from the box")
    return 0


def run_compare_client(out: SafePrinter, timeout: float) -> int:
    """Compare the access token's client_id with the web one, and renew once only if they differ."""
    tokens = ask_for_both_tokens(out, timeout)
    if tokens is None:
        return 1
    access_token, refresh_token = tokens
    verdict, claim_client_id = compare_client(access_token, scrape_client_id())
    if claim_client_id:
        # Never printed on purpose, but masked in case an error message echoes it.
        out.secrets.append(claim_client_id)
    out.heading("client_id comparison")
    out.line(f"access token's client_id claim vs the web client_id: {verdict}")
    if claim_client_id is None:
        out.line("The access token has no client_id claim, so the two can't be compared.")
        return 1
    if verdict == "same":
        out.line("The token names the web client_id itself, so no other client can be tried.")
        out.line("That points to SoundCloud's server being the only one that can renew it.")
        return 0
    out.line("The token names a different client_id. Renewing once with that client_id.")
    renew_and_print(out, refresh_token, claim_client_id, max_calls=1, source="from the box")
    return 0


def ask_for_both_tokens(out: SafePrinter, timeout: float) -> tuple[str, str] | None:
    """Ask for the access token, then the refresh token from the same sign-in."""
    out.line(f"Opening box 1 of 2 (access token). Waiting up to {timeout:.0f} s.")
    refused: list[str] = []
    access_token = ask_for_token(
        timeout,
        title=f"{REFRESH_BOX_TITLE}: access token",
        instructions=ACCESS_BOX_INSTRUCTIONS,
        reject=reject_non_access_token,
        rejected=refused,
    )
    out.line(f"pastes refused in box 1: {len(refused)}")
    if not access_token:
        out.line("No access token was accepted, so no renewal call was made.")
        return None
    out.secrets.append(access_token)
    out.line(f"access token: {describe_token(access_token)}")
    out.line(f"Opening box 2 of 2 (refresh token). Waiting up to {timeout:.0f} s.")
    refused.clear()
    refresh_token = ask_for_token(
        timeout,
        title=f"{REFRESH_BOX_TITLE}: refresh token",
        instructions=REFRESH_BOX_INSTRUCTIONS,
        reject=reject_access_token,
        rejected=refused,
    )
    out.line(f"pastes refused in box 2: {len(refused)}")
    if not refresh_token:
        out.line("No refresh token was accepted, so no renewal call was made.")
        return None
    out.secrets.append(refresh_token)
    out.line(f"refresh token: {describe_token(refresh_token)}")
    return access_token, refresh_token


def renew_with_claim(
    out: SafePrinter, access_token: str, refresh_token: str, max_calls: int, source: str
) -> None:
    """Renew with the client_id the access token names, which the spike found is the one needed."""
    client_id = access_token_client_id(access_token)
    if client_id is None:
        out.heading("Token refresh")
        out.line("The access token has no client_id claim, so no renewal call was made.")
        return
    # Never printed on purpose, but masked in case an error message echoes it.
    out.secrets.append(client_id)
    renew_and_print(out, refresh_token, client_id, max_calls, source)


def renew_and_print(
    out: SafePrinter, refresh_token: str, client_id: str, max_calls: int, source: str
) -> None:
    """Renew the access token with the refresh token and record what came back."""
    out.heading("Token refresh")
    out.line("Endpoint: POST https://secure.soundcloud.com/oauth/token, grant_type=refresh_token.")
    out.line("client_id: the one named in the access token's client_id claim (not printed).")
    try:
        calls, secrets = run_refresh_test(
            client_id, refresh_token, max_calls=max_calls, source=source
        )
    except Exception:  # Print through the redacting printer, not Python's raw traceback.
        out.line(traceback.format_exc())
        return
    out.secrets.extend(secrets)
    for call in calls:
        print_refresh_call(out, call)


def run_token_checks(out: SafePrinter, token: str) -> None:
    """Run the /me and yt-dlp checks, then drop the copy of the token yt-dlp keeps."""
    try:
        if not print_me_checks(out, token):
            out.line("SoundCloud rejected the token on every /me attempt, so the checks stop here.")
            return
        print_ytdlp_checks(out, token)
    except Exception:  # Print through the redacting printer, not Python's raw traceback.
        out.line(traceback.format_exc())
    finally:
        forget_ytdlp_signin()


if __name__ == "__main__":
    sys.exit(main())
