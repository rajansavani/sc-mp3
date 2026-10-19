"""Opens SoundCloud's sign-in page in a pywebview window and waits for the oauth_token cookie."""

import json
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import Morsel, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

import webview
from redact import error_details, redact_text, redact_url
from webview.errors import WebViewException
from webview.models import Request, Response
from webview2_driver import WebView2Driver

SIGNIN_URL = "https://soundcloud.com/signin"
WINDOW_TITLE = "Sign in to SoundCloud"
TOKEN_COOKIE = "oauth_token"
REFRESH_COOKIE = "oauth_refresh_token"
POLL_SECONDS = 1.0
FORM_TIMEOUT_SECONDS = 45.0
WEB_AUTH_PREFIX = "https://secure.soundcloud.com/web-auth"
LOADING_HTML = "<p style='font-family: sans-serif'>Loading SoundCloud sign-in.</p>"
# Printed text is cut to these lengths, always after redaction.
MAX_FORM_TEXT = 1000
MAX_BODY_TEXT = 160
# The sign-in form is an iframe from secure.soundcloud.com inside the soundcloud.com page.
FORM_PRESENT_JS = "!!document.querySelector('iframe[src*=\"secure.soundcloud.com/web-auth\"]')"
# Reads headings, error messages and captcha frames. It reads visible text only, never inputs.
FORM_MESSAGES_JS = """
JSON.stringify((() => {
  const shown = el => el.checkVisibility() && el.innerText && el.innerText.trim();
  const texts = selector => [...new Set([...document.querySelectorAll(selector)]
    .filter(shown).map(el => el.innerText.trim()))];
  const host = f => { try { return new URL(f.src).host; } catch { return ''; } };
  const captchas = [...document.querySelectorAll('iframe')]
    .filter(f => f.checkVisibility() && /captcha|arkose|challenge/i.test(f.title + ' ' + host(f)))
    .map(f => `${f.title} (${host(f)})`);
  const errorSelector =
    '[role=alert], [aria-live]:not([aria-live=off]), [class*=error i], [id*=error i]';
  return {headings: texts('h1, h2'), errors: texts(errorSelector), captchas: [...new Set(captchas)]}
})())
"""
# Captcha and bot-check services, so a challenge shows up in the timeline.
CAPTCHA_HOST_PARTS = ("captcha-delivery.com", "arkoselabs.com", "hcaptcha.com", "cloudflare.com")
RECAPTCHA_STEPS = ("anchor", "bframe", "reload", "userverify")
# Requests to these hosts show the sign-in flow; the rest are ads, analytics and assets.
FLOW_HOSTS = frozenset(
    {
        "soundcloud.com",
        "secure.soundcloud.com",
        "api-auth.soundcloud.com",
        "accounts.google.com",
        "appleid.apple.com",
        "www.facebook.com",
    }
)
ASSET_SUFFIXES = (".js", ".css", ".json", ".png", ".jpg", ".svg", ".woff2", ".ico")


@dataclass
class CookieFacts:
    """A cookie's attributes, without its value."""

    name: str
    domain: str
    path: str
    secure: bool
    httponly: bool
    samesite: str
    expires: str


@dataclass
class SigninOutcome:
    """What the window saw. The token is excluded from repr so it never gets printed by accident."""

    token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    form_seen_at: float | None = None
    token_seen_at: float | None = None
    token_seen_by: str = ""
    token_cookie: CookieFacts | None = None
    refresh_cookie: CookieFacts | None = None
    cookies_at_token: list[CookieFacts] = field(default_factory=list)
    polls: int = 0
    slowest_poll: float = 0.0
    slowest_poll_at: float = 0.0
    poll_errors: list[str] = field(default_factory=list)
    browser_opens: list[str] = field(default_factory=list)
    form_messages: list[str] = field(default_factory=list)
    signin_failures: list[str] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    stop_reason: str = ""


class SigninWatcher:
    """Polls the window's cookies once a second and on every page load until the token shows up."""

    def __init__(self, window: webview.Window, timeout: float) -> None:
        self.window = window
        self.driver = WebView2Driver(window)
        self.timeout = timeout
        self.outcome = SigninOutcome()
        self.started = time.monotonic()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.last_url = ""
        self.last_messages: dict[str, str] = {}

    def attach(self) -> None:
        """Subscribe to the window events the watcher needs."""
        events = self.window.events
        events.closed += self.closed.set
        events.loaded += self.on_loaded
        events.request_sent += self.on_request
        events.response_received += self.on_response

    def run(self) -> None:
        """Wait for the form, then the token, then close the window. Runs in a pywebview thread."""
        try:
            self.open_signin_page()
            if self.wait_for_form():
                self.poll_until_token()
        finally:
            if not self.closed.is_set():
                self.window.destroy()

    def open_signin_page(self) -> None:
        # Hook into WebView2 before SoundCloud loads, so the iframe and its responses are seen.
        self.window.events.loaded.wait(20)
        self.driver.attach()
        self.driver.watch_response_bodies(is_signin_failure, self.on_signin_failure)
        self.window.load_url(SIGNIN_URL)

    def wait_for_form(self) -> bool:
        deadline = self.started + FORM_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not self.closed.is_set():
            if self.window.evaluate_js(FORM_PRESENT_JS) is True:
                self.outcome.form_seen_at = self.elapsed()
                self.note("sign-in form iframe is in the page")
                return True
            time.sleep(POLL_SECONDS)
        self.outcome.stop_reason = "the sign-in form didn't appear"
        return False

    def poll_until_token(self) -> None:
        deadline = self.started + self.timeout
        while self.outcome.token is None:
            if self.closed.is_set():
                self.outcome.stop_reason = "the window was closed before sign-in"
                return
            if time.monotonic() > deadline:
                self.outcome.stop_reason = f"no sign-in within {self.timeout:.0f} s"
                return
            self.note_url_change()
            self.check_cookies("poll")
            self.check_form_messages()
            time.sleep(POLL_SECONDS)
        self.outcome.stop_reason = "token found"

    def check_form_messages(self) -> None:
        """Record what the page and the sign-in iframe show, whenever it changes."""
        self.note_messages("page", lambda: self.window.evaluate_js(FORM_MESSAGES_JS))
        frame = self.driver.find_frame(WEB_AUTH_PREFIX)
        if frame is not None:
            self.note_messages("form", lambda: self.driver.frame_eval(frame, FORM_MESSAGES_JS))

    def note_messages(self, place: str, read: Callable[[], Any]) -> None:
        try:
            raw = read()
        except (RuntimeError, TimeoutError, WebViewException):
            return  # A navigation replaced the document; the next poll reads the new one.
        # Redact the whole text before cutting it, so no email or token is split past a pattern.
        text = redact_text(str(raw or ""))[:MAX_FORM_TEXT]
        if text == self.last_messages.get(place):
            return
        self.last_messages[place] = text
        self.outcome.form_messages.append(f"{self.elapsed():7.2f}s {place}: {text}")
        self.note(f"{place} shows: {text}")

    def on_signin_failure(self, url: str, status: int, body: str | None) -> None:
        summary = f"{status} {redact_url(url)}: {describe_error_body(body)}"
        self.outcome.signin_failures.append(f"{self.elapsed():7.2f}s {summary}")
        self.note(f"sign-in failed, {summary}")

    def on_loaded(self) -> None:
        self.note(f"page loaded: {redact_url(self.window.get_current_url() or '')}")
        self.check_cookies("loaded")

    def on_request(self, request: Request) -> None:
        if is_flow_url(request.url):
            self.note(f"-> {request.method} {redact_url(request.url)}")

    def on_response(self, response: Response) -> None:
        if is_flow_url(response.url):
            self.note(f"<- {response.status_code} {redact_url(response.url)}")

    def record_browser_open(self, url: str) -> None:
        self.outcome.browser_opens.append(redact_url(url))
        self.note(f"pywebview handed a link to the system browser: {redact_url(url)}")

    def note_url_change(self) -> None:
        url = self.window.get_current_url() or ""
        if url != self.last_url:
            self.last_url = url
            self.note(f"window URL is now {redact_url(url)}")

    def check_cookies(self, source: str) -> None:
        polled_at = time.monotonic()
        try:
            cookies = self.window.get_cookies()
        except Exception as error:  # pywebview raises its own and .NET errors here; keep polling.
            self.outcome.poll_errors.append(f"{type(error).__name__}: {redact_text(str(error))}")
            return
        with self.lock:
            self.outcome.polls += 1
            duration = time.monotonic() - polled_at
            if duration > self.outcome.slowest_poll:
                self.outcome.slowest_poll = duration
                self.outcome.slowest_poll_at = self.elapsed()
            if self.outcome.token is not None:
                return
            morsel = find_cookie(cookies, TOKEN_COOKIE)
            if morsel is None:
                return
            self.outcome.token = morsel.value
            self.outcome.token_seen_at = self.elapsed()
            self.outcome.token_seen_by = source
            self.outcome.token_cookie = describe_cookie(morsel)
            self.outcome.cookies_at_token = [describe_cookie(m) for m in all_morsels(cookies)]
            refresh = find_cookie(cookies, REFRESH_COOKIE)
            if refresh is not None:
                self.outcome.refresh_token = refresh.value
                self.outcome.refresh_cookie = describe_cookie(refresh)
        self.note(f"oauth_token cookie seen by the {source} check")

    def note(self, text: str) -> None:
        self.outcome.timeline.append(f"{self.elapsed():7.2f}s  {text}")

    def elapsed(self) -> float:
        return time.monotonic() - self.started


def is_flow_url(url: str) -> bool:
    """True for sign-in and captcha requests, rather than ads, analytics or assets."""
    parts = urlsplit(url)
    if any(part in parts.netloc for part in CAPTCHA_HOST_PARTS):
        return True
    if "/recaptcha/" in parts.path:
        return any(step in parts.path for step in RECAPTCHA_STEPS)
    if parts.netloc not in FLOW_HOSTS:
        return False
    # Google's sign-in helper script and Facebook's tracking pixel load on every page.
    if parts.path.startswith(("/gsi/", "/tr")):
        return False
    return not parts.path.endswith(ASSET_SUFFIXES)


def is_signin_failure(url: str, status: int) -> bool:
    """True for a rejected password sign-in. Bodies of successful ones carry the token."""
    parts = urlsplit(url)
    return parts.netloc == "api-auth.soundcloud.com" and parts.path == "/sign-in" and status >= 400


def describe_error_body(body: str | None) -> str:
    """Summarize an error response body: its error codes and messages, redacted."""
    if not body:
        return "empty body"
    try:
        parsed = json.loads(body)
    except ValueError:
        return f"non-JSON body, {len(body)} chars: {redact_text(body)[:MAX_BODY_TEXT]!r}"
    details = error_details(parsed)
    if details:
        return json.dumps(details)
    keys = sorted(parsed) if isinstance(parsed, dict) else type(parsed).__name__
    return f"JSON with no error fields, keys {keys}"


def all_morsels(cookies: list[SimpleCookie]) -> list[Morsel]:
    """Flatten pywebview's cookie list, which holds one SimpleCookie per cookie."""
    return [morsel for jar in cookies for morsel in jar.values()]


def find_cookie(cookies: list[SimpleCookie], name: str) -> Morsel | None:
    """Return the named cookie for a soundcloud.com domain, if the window has it."""
    for morsel in all_morsels(cookies):
        if morsel.key == name and morsel["domain"].endswith("soundcloud.com"):
            return morsel
    return None


def describe_cookie(morsel: Morsel) -> CookieFacts:
    return CookieFacts(
        name=morsel.key,
        domain=morsel["domain"],
        path=morsel["path"],
        secure=bool(morsel["secure"]),
        httponly=bool(morsel["httponly"]),
        samesite=str(morsel["samesite"] or ""),
        expires=morsel["expires"],
    )


def run_signin_window(timeout: float) -> SigninOutcome:
    """Show the sign-in window in private mode and return what it saw once it closes."""
    # The window starts blank so the watcher can hook into WebView2 before SoundCloud loads.
    window = webview.create_window(WINDOW_TITLE, html=LOADING_HTML, width=1000, height=800)
    watcher = SigninWatcher(window, timeout)
    watcher.attach()
    original_open = webbrowser.open

    # pywebview sends popups to webbrowser.open. Record them instead of opening blank browser tabs.
    def record_only(url: str, *_args: object, **_kwargs: object) -> bool:
        watcher.record_browser_open(url)
        return True

    webbrowser.open = record_only
    try:
        webview.start(watcher.run, private_mode=True)
    finally:
        webbrowser.open = original_open
    return watcher.outcome
