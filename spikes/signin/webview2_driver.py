"""Drives a pywebview WebView2 window: script in iframes, real clicks, screenshots. Windows only."""

import base64
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import clr  # noqa: F401  # Loading pythonnet makes the System namespaces below importable.
import webview
from System import Action, String
from System.IO import Stream, StreamReader
from System.Threading.Tasks import Task, TaskScheduler

TASK_TIMEOUT_SECONDS = 15.0
FIND_ELEMENT_JS = """
(label => {
  const candidates = [...document.querySelectorAll('button, a, [role=button]')];
  const onScreen = r => r.width > 0 && r.top >= 0 && r.left >= 0
    && r.bottom <= innerHeight && r.right <= innerWidth;
  const visible = candidates.filter(
    el => el.checkVisibility() && el.innerText && onScreen(el.getBoundingClientRect()));
  const el = visible.find(el => el.innerText.trim().startsWith(label));
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, tag: el.tagName};
})
"""


class WebView2Driver:
    """Reaches into the WebView2 control behind a pywebview window, which evaluate_js can't do."""

    def __init__(self, window: webview.Window) -> None:
        self.window = window
        self.frames: list[Any] = []

    def attach(self) -> None:
        """Start collecting iframes. Call after the window has loaded its first page."""

        def subscribe() -> None:
            core = self.core()
            core.FrameCreated += self.on_frame_created

        self.on_ui_thread(subscribe)

    def on_frame_created(self, _sender: Any, args: Any) -> None:
        self.frames.append(args.Frame)

    def watch_response_bodies(
        self,
        wanted: Callable[[str, int], bool],
        on_body: Callable[[str, int, str | None], None],
    ) -> None:
        """Call `on_body(url, status, text)` for every response that `wanted(url, status)` picks."""

        def on_response(_sender: Any, args: Any) -> None:
            url, status = str(args.Request.Uri), int(args.Response.StatusCode)
            if not wanted(url, status):
                return
            # Continue on the UI thread, where WebView2's content stream may be read.
            ui = TaskScheduler.FromCurrentSynchronizationContext()

            def read(task: Any) -> None:
                stream = None if task.IsFaulted else task.Result
                on_body(url, status, StreamReader(stream).ReadToEnd() if stream else None)

            args.Response.GetContentAsync().ContinueWith(Action[Task[Stream]](read), ui)

        def subscribe() -> None:
            core = self.core()
            core.WebResourceResponseReceived += on_response

        self.on_ui_thread(subscribe)

    def core(self) -> Any:
        return self.window.native.browser.webview.CoreWebView2

    def on_ui_thread(self, action: Callable[[], None]) -> None:
        # WebView2 objects may only be touched on the WinForms UI thread.
        self.window.native.Invoke(Action(action))

    def await_task(self, start: Callable[[], Any]) -> str:
        """Start a WebView2 Task[String] on the UI thread and wait here for its result."""
        done = threading.Event()
        box: dict[str, Any] = {}

        def on_done(task: Any) -> None:
            if task.IsFaulted:
                box["error"] = str(task.Exception)
            else:
                box["value"] = task.Result
            done.set()

        try:
            self.on_ui_thread(lambda: start().ContinueWith(Action[Task[String]](on_done)))
        except Exception as error:
            # A destroyed frame or a closed window throws a .NET exception through pythonnet.
            # Turn it into RuntimeError, which every caller here already handles.
            raise RuntimeError(f"WebView2 call failed: {type(error).__name__}") from None
        if not done.wait(TASK_TIMEOUT_SECONDS):
            raise TimeoutError("WebView2 didn't answer in time")
        if "error" in box:
            raise RuntimeError(box["error"])
        return box["value"]

    def page_eval(self, script: str) -> Any:
        """Run script in the top page through DevTools, which works without pywebview's bridge."""
        result = self.cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return result.get("result", {}).get("value")

    def frame_eval(self, frame: Any, script: str) -> Any:
        """Run script in one iframe and return its JSON result."""
        return json.loads(self.await_task(lambda: frame.ExecuteScriptAsync(script)))

    def find_frame(self, url_prefix: str) -> Any | None:
        """Return the newest live iframe whose location starts with `url_prefix`."""
        for frame in reversed(self.frames):
            try:
                href = self.frame_eval(frame, "location.href")
            except (RuntimeError, TimeoutError):
                continue  # The frame was destroyed by a navigation.
            if isinstance(href, str) and href.startswith(url_prefix):
                return frame
        return None

    def find_element(self, label: str, frame: Any | None = None) -> dict[str, Any] | None:
        """Find a visible button or link whose text starts with `label`, in a frame or the page."""
        script = f"JSON.stringify({FIND_ELEMENT_JS}({json.dumps(label)}))"
        raw = self.page_eval(script) if frame is None else self.frame_eval(frame, script)
        return json.loads(raw) if raw else None

    def click(self, x: float, y: float) -> None:
        """Click at page coordinates with real input events, which also count as a user gesture."""
        for event_type in ("mousePressed", "mouseReleased"):
            params = {"type": event_type, "x": x, "y": y, "button": "left", "clickCount": 1}
            self.cdp("Input.dispatchMouseEvent", params)

    def screenshot(self, path: Path) -> None:
        result = self.cdp("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))

    def cdp(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call a Chrome DevTools Protocol method through WebView2, without opening a debug port."""
        payload = json.dumps(params)
        raw = self.await_task(lambda: self.core().CallDevToolsProtocolMethodAsync(method, payload))
        return json.loads(raw)
