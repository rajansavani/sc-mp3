"""A small client for the browser's own DevTools endpoint. It never attaches to a page."""

import json
import time
import urllib.request
from typing import Any
from urllib.parse import quote

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection, connect

CALL_TIMEOUT = 10.0  # seconds
TITLE_PREFIX = "webdriver "
WEBDRIVER_PAGE = "data:text/html," + quote(
    f"<script>document.title = '{TITLE_PREFIX}' + navigator.webdriver</script>"
)
TITLE_TRIES = 30


class DevToolsError(Exception):
    """A DevTools call failed, or the connection to the browser is gone."""


class BrowserGone(DevToolsError):
    """The browser closed the DevTools connection, so it has exited or is exiting."""


class DevTools:
    """Sends commands to the browser target and returns their results."""

    def __init__(self, port: int, browser_path: str) -> None:
        url = f"ws://127.0.0.1:{port}{browser_path}"
        # Cookie lists can be large once Google has set its own, so there's no size limit.
        self._socket: ClientConnection = connect(url, max_size=None, open_timeout=CALL_TIMEOUT)
        self._next_id = 0

    def call(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        """Send one command and wait for its answer, skipping any events in between."""
        self._next_id += 1
        message_id = self._next_id
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self._socket.send(json.dumps(message))
            deadline = time.monotonic() + CALL_TIMEOUT
            while True:
                answer = json.loads(self._socket.recv(timeout=deadline - time.monotonic()))
                if answer.get("id") == message_id:
                    break
        except ConnectionClosed as error:
            raise BrowserGone(f"the DevTools connection closed during {method}") from error
        except TimeoutError as error:
            raise DevToolsError(f"{method} got no answer within {CALL_TIMEOUT:.0f} s") from error
        if "error" in answer:
            # DevTools errors name the problem, such as an unknown method, and hold no cookies.
            raise DevToolsError(f"{method} failed: {answer['error'].get('message')}")
        return answer.get("result", {})

    def close(self) -> None:
        self._socket.close()


def close_browser(devtools: DevTools) -> str:
    """Send Browser.close and close the connection. Returns what happened, for the output."""
    try:
        devtools.call("Browser.close")
    except BrowserGone:
        # The browser often drops the connection before it answers Browser.close.
        return "Browser.close: the connection closed as the browser exited"
    except DevToolsError as error:
        return f"Browser.close failed: {error}"
    finally:
        devtools.close()
    return "Browser.close: answered"


def version(port: int) -> dict[str, Any]:
    """The browser's /json/version answer, which any local process can read without a key."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as answer:
        return json.loads(answer.read())


def refuses_web_origin(port: int, browser_path: str) -> bool:
    """True when the browser refuses a WebSocket that says it comes from a web page."""
    url = f"ws://127.0.0.1:{port}{browser_path}"
    try:
        with connect(url, origin="https://example.com", open_timeout=CALL_TIMEOUT):
            return False
    except InvalidStatus:
        return True


def webdriver_flag(devtools: DevTools) -> str:
    """navigator.webdriver as a page sees it, read from a new tab's title. Self-test only.

    Nothing attaches to any tab: the page writes the value into its own title.
    """
    target = devtools.call("Target.createTarget", {"url": WEBDRIVER_PAGE, "background": True})
    target_id = target["targetId"]
    try:
        for _ in range(TITLE_TRIES):
            for info in devtools.call("Target.getTargets").get("targetInfos", []):
                title = info.get("title", "")
                if info.get("targetId") == target_id and title.startswith(TITLE_PREFIX):
                    return title.removeprefix(TITLE_PREFIX)
            time.sleep(0.1)
        return "not read"
    finally:
        devtools.call("Target.closeTarget", {"targetId": target_id})
