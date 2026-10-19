"""Automated checks that need no account: the home page's Sign in button and "Continue with"."""

import json
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import webview
from login_window import FORM_MESSAGES_JS, SIGNIN_URL, WEB_AUTH_PREFIX, is_flow_url
from redact import redact_url
from webview.errors import WebViewException
from webview.models import Request
from webview2_driver import WebView2Driver

HOME_URL = "https://soundcloud.com/"
WINDOW_TITLE = "sc-mp3 spike: automated check, please don't type here"
SETTLE_SECONDS = 4.0
FIND_TIMEOUT_SECONDS = 15.0
# Google goes last: after its popup, pywebview's loaded event stopped firing in one run.
PROVIDERS = ("Apple", "Facebook", "Google")
NAVIGATION_WAIT_SECONDS = 10.0
FORM_VISIBLE_JS = """
JSON.stringify((() => {
  const f = document.querySelector('iframe[src*="secure.soundcloud.com/web-auth"]');
  if (!f) return null;
  const r = f.getBoundingClientRect();
  return {visible: f.checkVisibility(), x: r.left, y: r.top, width: r.width, height: r.height};
})())
"""


@dataclass
class ProbeStep:
    """What one automated action did."""

    label: str
    found_target: bool = False
    form_before: Any = None
    url_after: str = ""
    form_after: Any = None
    frame_url_after: str = ""
    browser_opens: list[str] = field(default_factory=list)
    flow_requests: list[str] = field(default_factory=list)
    form_messages: Any = None
    screenshot: str = ""
    error: str = ""


class Probe:
    """Runs each check in turn in one window, recording what happened after every action."""

    def __init__(self, window: webview.Window, shots_dir: Path | None) -> None:
        self.window = window
        self.driver = WebView2Driver(window)
        self.shots_dir = shots_dir
        self.steps: list[ProbeStep] = []
        self.opened_urls: list[str] = []
        self.opened_before_step = 0
        self.requests: list[str] = []

    def record_browser_open(self, url: str, *_args: object, **_kwargs: object) -> bool:
        """Stand-in for webbrowser.open: record the link instead of opening the user's browser."""
        self.opened_urls.append(redact_url(url))
        return True

    def on_request(self, request: Request) -> None:
        if is_flow_url(request.url):
            self.requests.append(f"{request.method} {redact_url(request.url)}")

    def run(self) -> None:
        checks: list[tuple[str, Callable[[], None]]] = [
            ("load /signin", self.check_signin_page),
            ("click Sign in on the home page", self.check_home_button),
        ]
        checks += [
            (f"click Continue with {p}", lambda p=p: self.check_provider(p)) for p in PROVIDERS
        ]
        try:
            self.window.events.loaded.wait(20)
            self.driver.attach()
            for label, check in checks:
                self.run_check(label, check)
        finally:
            self.window.destroy()

    def run_check(self, label: str, check: Callable[[], None]) -> None:
        try:
            check()
        except (RuntimeError, TimeoutError, WebViewException, OSError) as error:
            # Record the failure and go on, so one stuck page doesn't hide the other results.
            self.steps.append(ProbeStep(label, error=f"{type(error).__name__}: {error}"))

    def check_signin_page(self) -> None:
        step = self.start_step("load /signin")
        self.navigate(SIGNIN_URL)
        frame = self.wait_for_form_frame()
        step.found_target = frame is not None
        self.finish_step(step, "signin-page", frame)

    def check_home_button(self) -> None:
        step = self.start_step("click Sign in on the home page")
        self.navigate(HOME_URL)
        target = self.wait_for(lambda: self.driver.find_element("Sign in"))
        # The home page already holds a hidden sign-in iframe, so compare before and after.
        step.form_before = self.form_state()
        if target:
            step.found_target = True
            self.driver.click(target["x"], target["y"])
        self.finish_step(step, "home-sign-in")

    def check_provider(self, provider: str) -> None:
        step = self.start_step(f"click Continue with {provider}")
        self.navigate(SIGNIN_URL)
        frame = self.wait_for_form_frame()
        form = self.form_state()
        label = f"Continue with {provider}"
        target = self.wait_for(lambda: self.driver.find_element(label, frame)) if frame else None
        if target and form:
            step.found_target = True
            # Button coordinates are inside the iframe, so add the iframe's offset in the page.
            self.driver.click(form["x"] + target["x"], form["y"] + target["y"])
        self.finish_step(step, f"provider-{provider.lower()}", frame)

    def start_step(self, label: str) -> ProbeStep:
        self.opened_before_step = len(self.opened_urls)
        self.requests.clear()
        return ProbeStep(label)

    def finish_step(self, step: ProbeStep, shot_name: str, frame: Any | None = None) -> None:
        time.sleep(SETTLE_SECONDS)
        step.url_after = redact_url(self.driver.page_eval("location.href") or "")
        step.form_after = self.form_state()
        if frame is not None:
            step.frame_url_after = self.frame_location(frame)
            step.form_messages = self.frame_messages(frame)
        step.browser_opens = self.opened_urls[self.opened_before_step :]
        step.flow_requests = list(self.requests)
        if self.shots_dir is not None:
            path = self.shots_dir / f"{len(self.steps) + 1}-{shot_name}.png"
            self.driver.screenshot(path)
            step.screenshot = f"{self.shots_dir.name}/{path.name}"
        self.steps.append(step)

    def navigate(self, url: str) -> None:
        self.driver.frames.clear()
        self.window.load_url(url)
        # The checks poll for what they need, so a missing loaded event only costs this wait.
        self.window.events.loaded.wait(NAVIGATION_WAIT_SECONDS)

    def form_state(self) -> dict[str, Any] | None:
        raw = self.driver.page_eval(FORM_VISIBLE_JS)
        return json.loads(raw) if raw else None

    def frame_location(self, frame: Any) -> str:
        try:
            return redact_url(self.driver.frame_eval(frame, "location.href"))
        except (RuntimeError, TimeoutError) as error:
            return f"unreadable ({type(error).__name__})"

    def frame_messages(self, frame: Any) -> Any:
        try:
            return json.loads(self.driver.frame_eval(frame, FORM_MESSAGES_JS))
        except (RuntimeError, TimeoutError) as error:
            return f"unreadable ({type(error).__name__})"

    def wait_for_form_frame(self) -> Any | None:
        return self.wait_for(lambda: self.driver.find_frame(WEB_AUTH_PREFIX))

    def wait_for(self, find: Callable[[], Any]) -> Any:
        deadline = time.monotonic() + FIND_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                found = find()
            except (RuntimeError, TimeoutError):
                found = None  # The frame went away mid-call; the next try finds the new one.
            if found:
                return found
            time.sleep(0.5)
        return None


def new_shots_folder(base: Path) -> Path:
    """Create a fresh subfolder for one run's screenshots, so earlier runs are never replaced."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for attempt in range(100):
        folder = base / (f"run-{stamp}" if attempt == 0 else f"run-{stamp}-{attempt}")
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return folder
    raise FileExistsError(f"no free screenshot folder name under {base}")


def run_probe(shots_dir: Path | None) -> list[ProbeStep]:
    """Open a private window, run every check, and return the steps once it closes."""
    if shots_dir is not None:
        shots_dir = new_shots_folder(shots_dir)
    window = webview.create_window(WINDOW_TITLE, html="<p>Starting the automated check.</p>")
    probe = Probe(window, shots_dir)
    window.events.request_sent += probe.on_request
    original_open = webbrowser.open
    webbrowser.open = probe.record_browser_open
    try:
        webview.start(probe.run, private_mode=True)
    finally:
        webbrowser.open = original_open
    return probe.steps
