"""Integration tests for the RIVA service.

Starts the service on a temp socket, sends JSON-RPC requests, verifies responses.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from riva.service import start_server


async def _send_rpc(
    socket_path: Path,
    method: str,
    params: dict | None = None,
    id: int = 1,
) -> dict:
    """Send a JSON-RPC request to the RIVA socket and return the response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": id,
    }).encode("utf-8")

    writer.write(struct.pack("!I", len(request)))
    writer.write(request)
    await writer.drain()

    length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=5.0)
    msg_length = struct.unpack("!I", length_bytes)[0]
    data = await asyncio.wait_for(reader.readexactly(msg_length), timeout=5.0)

    writer.close()
    await writer.wait_closed()

    return json.loads(data.decode("utf-8"))


@pytest.fixture
async def riva_service(tmp_path):
    """Start a RIVA service on a temp socket for testing."""
    socket_path = tmp_path / "test_riva.sock"

    # Patch settings.data_dir so schema creates tables in tmp_path
    with patch("riva.db.settings") as mock_settings, \
         patch("riva.service.settings") as mock_service_settings:
        mock_settings.data_dir = tmp_path
        mock_service_settings.data_dir = tmp_path

        ready = asyncio.Event()
        server_task = asyncio.create_task(
            start_server(socket_path=socket_path, ready_event=ready)
        )

        await asyncio.wait_for(ready.wait(), timeout=5.0)
        yield socket_path

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


class TestServiceIntegration:
    """Integration tests using a real socket."""

    @pytest.mark.asyncio
    async def test_ping(self, riva_service):
        """riva/ping returns pong over the socket."""
        response = await _send_rpc(riva_service, "riva/ping")
        assert response["result"]["result"] == "pong"

    @pytest.mark.asyncio
    async def test_status(self, riva_service):
        """riva/status returns running status over the socket."""
        response = await _send_rpc(riva_service, "riva/status")
        assert response["result"]["status"] == "running"
        assert response["result"]["version"] == "0.2.0"

    @pytest.mark.asyncio
    async def test_unknown_method(self, riva_service):
        """Unknown method returns error over the socket."""
        response = await _send_rpc(riva_service, "riva/nonexistent")
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_multiple_requests(self, riva_service):
        """Multiple sequential requests work on the same socket."""
        r1 = await _send_rpc(riva_service, "riva/ping", id=1)
        r2 = await _send_rpc(riva_service, "riva/status", id=2)

        assert r1["result"]["result"] == "pong"
        assert r1["id"] == 1
        assert r2["result"]["status"] == "running"
        assert r2["id"] == 2
