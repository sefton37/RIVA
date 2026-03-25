"""Integration test: Cairn proxy -> RIVA service end-to-end.

Starts a RIVA service in a background thread, then tests the Cairn
proxy handler forwarding synchronous socket requests to it.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from unittest.mock import patch

import pytest

from riva.service import start_server


@pytest.fixture
def riva_with_proxy(tmp_path):
    """Start RIVA service in a background thread and configure Cairn proxy."""
    socket_path = tmp_path / "test_riva.sock"
    ready = threading.Event()
    loop = None

    def _run_server():
        nonlocal loop
        loop = asyncio.new_event_loop()

        async def _start():
            aio_ready = asyncio.Event()

            async def _signal_ready():
                await aio_ready.wait()
                ready.set()

            with patch("riva.db.settings") as mock_db, \
                 patch("riva.service.settings") as mock_svc:
                mock_db.data_dir = tmp_path
                mock_svc.data_dir = tmp_path

                server_task = loop.create_task(
                    start_server(socket_path=socket_path, ready_event=aio_ready)
                )
                signal_task = loop.create_task(_signal_ready())
                await asyncio.gather(server_task, signal_task)

        loop.run_until_complete(_start())

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()

    # Wait for server to be ready
    assert ready.wait(timeout=5.0), "RIVA service did not start in time"

    # Import and patch the Cairn proxy to use our temp socket
    sys.path.insert(0, "/home/kellogg/dev/Cairn/src")
    import cairn.rpc_handlers.riva as riva_proxy

    original = riva_proxy._get_socket_path
    riva_proxy._get_socket_path = lambda: socket_path

    yield riva_proxy

    riva_proxy._get_socket_path = original
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)


class TestProxyIntegration:
    """End-to-end: Cairn proxy -> RIVA socket -> response."""

    def test_proxy_health_check(self, riva_with_proxy):
        """Proxy forwards health check and returns pong."""
        proxy = riva_with_proxy
        result = proxy.handle_riva_proxy(
            method="riva/ping", params={}, req_id=42
        )
        assert result.get("result", {}).get("result") == "pong"
        assert result["id"] == 42

    def test_proxy_status(self, riva_with_proxy):
        """Proxy forwards status and returns running."""
        proxy = riva_with_proxy
        result = proxy.handle_riva_proxy(
            method="riva/status", params={}, req_id=43
        )
        assert result.get("result", {}).get("status") == "running"
        assert result.get("result", {}).get("version") == "0.1.0"

    def test_proxy_unknown_method(self, riva_with_proxy):
        """Proxy forwards unknown method and returns -32601."""
        proxy = riva_with_proxy
        result = proxy.handle_riva_proxy(
            method="riva/nonexistent", params={}, req_id=44
        )
        assert result.get("error", {}).get("code") == -32601

    def test_proxy_multiple_requests(self, riva_with_proxy):
        """Multiple sequential requests through the proxy."""
        proxy = riva_with_proxy
        r1 = proxy.handle_riva_proxy(method="riva/ping", params={}, req_id=1)
        r2 = proxy.handle_riva_proxy(method="riva/status", params={}, req_id=2)
        assert r1.get("result", {}).get("result") == "pong"
        assert r2.get("result", {}).get("status") == "running"
