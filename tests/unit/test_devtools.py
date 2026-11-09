"""Tests for the connection to a headless browser's DevTools, against a real local server."""

import threading
from collections.abc import Iterator

import pytest
from websockets.sync.server import Server, ServerConnection, serve

from scmp3.app import devtools

PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "WS_PROXY", "WSS_PROXY")


def echo(connection: ServerConnection) -> None:
    for message in connection:
        connection.send(message)


@pytest.fixture
def server() -> Iterator[Server]:
    """A WebSocket server on 127.0.0.1 that sends each message back, as a browser's port listens."""
    with serve(echo, "127.0.0.1", 0) as running:
        thread = threading.Thread(target=running.serve_forever, daemon=True)
        thread.start()
        yield running
        running.shutdown()
        thread.join(5)


def test_the_devtools_connection_never_goes_through_a_proxy(
    monkeypatch: pytest.MonkeyPatch, server: Server
) -> None:
    # A proxy that's set but can't be reached: going through it would fail the connection.
    for name in PROXY_VARIABLES:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
        monkeypatch.setenv(name.lower(), "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    port = server.socket.getsockname()[1]
    socket = devtools.open_socket(port, "/devtools/browser/made-up")
    try:
        socket.send("ping")
        assert socket.recv(timeout=5) == "ping"
    finally:
        socket.close()
