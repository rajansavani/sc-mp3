"""A small client for a headless browser's own DevTools endpoint. It never attaches to a page."""

import json
import logging
import time
from typing import Any, Protocol

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from scmp3.core.errors import ErrorKind, ScmpError

CALL_TIMEOUT = 10.0  # seconds

log = logging.getLogger(__name__)


class Socket(Protocol):
    """The parts of a websockets connection the client uses, so tests can pass a fake."""

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


def open_socket(port: int, browser_path: str) -> Socket:
    """The browser target's WebSocket on 127.0.0.1, where the headless browser listens."""
    try:
        # Never through a proxy: the answers hold every cookie in the profile, Google's included.
        # The cookie list can be large once Google has set its own, so there's no size limit.
        # BrowserDevTools closes the connection itself, which websockets calls its legacy use.
        return connect(
            f"ws://127.0.0.1:{port}{browser_path}",
            max_size=None,
            open_timeout=5,
            proxy=None,
            legacy=True,
        )
    except (OSError, WebSocketException) as error:
        raise ScmpError(
            ErrorKind.internal, f"can't reach the browser's DevTools: {error}"
        ) from error


class BrowserDevTools:
    """Sends commands to the browser target and returns their results."""

    def __init__(self, socket: Socket) -> None:
        self._socket = socket
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one command, and wait for its answer, skipping events."""
        self._next_id += 1
        message_id = self._next_id
        request: dict[str, Any] = {"id": message_id, "method": method}
        if params is not None:
            request["params"] = params
        try:
            self._socket.send(json.dumps(request))
            answer = self._answer_to(message_id)
        except (ConnectionClosed, TimeoutError) as error:
            raise ScmpError(
                ErrorKind.internal, f"the browser's DevTools didn't answer {method}"
            ) from error
        if "error" in answer:
            # DevTools errors name the problem, such as an unknown method, and hold no cookies.
            detail = answer["error"].get("message") if isinstance(answer["error"], dict) else None
            raise ScmpError(ErrorKind.internal, f"{method} failed: {detail}")
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    def _answer_to(self, message_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + CALL_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            answer = json.loads(self._socket.recv(timeout=remaining))
            if isinstance(answer, dict) and answer.get("id") == message_id:
                return answer

    def close_browser(self) -> None:
        """Ask the browser to close, then drop the connection, which it often closes first."""
        try:
            self._socket.send(json.dumps({"id": self._next_id + 1, "method": "Browser.close"}))
        except ConnectionClosed:
            log.debug("The browser closed its DevTools connection before Browser.close")
        finally:
            self._socket.close()
