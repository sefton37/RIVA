"""Shared fixtures for RIVA e2e benchmark tests.

Provides:
    riva_client  — Live RIVA service + RPC client
    ollama_provider — Real Ollama LLM provider
    agent_workspace — Git-initialized temp directory
    load_cases — YAML case loader
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from riva.schema import ensure_schema

_CASES_DIR = Path(__file__).parent / "benchmark_cases"


# ── RPC Client ──────────────────────────────────────────────────────


class RivaClient:
    """Synchronous JSON-RPC client for the RIVA Unix socket."""

    def __init__(self, sock_path: Path) -> None:
        self.sock_path = sock_path
        self._req_id = 0

    def call(self, method: str, **params: Any) -> Any:
        """Send an RPC request and return the result. Raises on error."""
        self._req_id += 1
        request = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._req_id,
        }).encode("utf-8")

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(60.0)
        s.connect(str(self.sock_path))

        # Send length-prefixed message
        s.sendall(struct.pack("!I", len(request)))
        s.sendall(request)

        # Read length-prefixed response
        length_bytes = self._recv_exactly(s, 4)
        msg_length = struct.unpack("!I", length_bytes)[0]
        data = self._recv_exactly(s, msg_length)
        s.close()

        response = json.loads(data.decode("utf-8"))
        if "error" in response and response["error"]:
            raise RuntimeError(
                f"RPC error {response['error'].get('code')}: {response['error'].get('message')}"
            )
        return response.get("result")

    def _recv_exactly(self, s: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Socket closed")
            buf.extend(chunk)
        return bytes(buf)


# ── YAML Loader ─────────────────────────────────────────────────────


def load_cases(filename: str) -> list[dict[str, Any]]:
    """Load test cases from a YAML file in benchmark_cases/."""
    path = _CASES_DIR / filename
    with open(path) as f:
        return yaml.safe_load(f)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def ollama_provider():
    """Real Ollama provider. Skips test if Ollama is unreachable."""
    try:
        from trcore.providers.ollama import OllamaProvider
        provider = OllamaProvider()
        health = provider.check_health()
        if not health.reachable:
            pytest.skip("Ollama not reachable")
        return provider
    except Exception as exc:
        pytest.skip(f"Cannot create Ollama provider: {exc}")


@pytest.fixture
def agent_workspace(tmp_path) -> Path:
    """Git-initialized temp directory for agent workspaces."""
    ws = tmp_path / "agent_workspace"
    ws.mkdir()
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.email", "test@test.com"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.name", "Test"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(ws), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
    )
    return ws


@pytest.fixture
def riva_client(tmp_path):
    """Start a real RIVA service in a background thread.

    Returns a RivaClient connected to the service's Unix socket.
    The service uses tmp_path for its database (isolated from production).
    """
    import sqlite3

    # Create isolated DB
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Create trcore tables needed by SimCCManager
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cc_agents (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            purpose TEXT,
            cwd TEXT NOT NULL,
            session_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cc_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.close()

    sock_path = tmp_path / "riva.sock"
    ready = threading.Event()

    # Get LLM provider — Anthropic, remote Ollama, or local Ollama
    provider = None
    bench_provider = os.environ.get("RIVA_BENCHMARK_PROVIDER")
    ollama_url = os.environ.get("RIVA_BENCHMARK_OLLAMA_URL")

    if bench_provider == "anthropic":
        try:
            from benchmarks.anthropic_provider import AnthropicBenchmarkProvider
            bench_model = os.environ.get("RIVA_BENCHMARK_MODEL", "claude-sonnet-4-20250514")
            provider = AnthropicBenchmarkProvider(model=bench_model)
        except Exception as exc:
            pytest.skip(f"Anthropic provider not available: {exc}")
    else:
        try:
            from trcore.providers.ollama import OllamaProvider
            # Bypass settings localhost check — pass URL directly to provider
            provider = OllamaProvider(url=ollama_url) if ollama_url else OllamaProvider()
            if not provider.check_health().reachable:
                provider = None
        except Exception:
            pass

    def _run_server():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _start():
            from riva.service import start_server
            ready_event = asyncio.Event()

            async def _signal_ready():
                await ready_event.wait()
                ready.set()

            asyncio.ensure_future(_signal_ready())
            use_structured = os.environ.get("RIVA_BENCHMARK_STRUCTURED") == "1"
            await start_server(
                provider=provider,
                socket_path=sock_path,
                ready_event=ready_event,
                simulated=True,  # Safe: no real Claude Code
                disable_guard=True,  # Benchmarks test plan quality, not guard quality
                structured=use_structured,
            )

        try:
            loop.run_until_complete(_start())
        except Exception:
            ready.set()  # Unblock even on failure
        finally:
            loop.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.service.settings") as mock_s2:
        mock_s.data_dir = tmp_path
        mock_s2.data_dir = tmp_path

        thread = threading.Thread(target=_run_server, daemon=True)
        thread.start()

        if not ready.wait(timeout=15):
            pytest.fail("RIVA service failed to start within 15 seconds")

        client = RivaClient(sock_path)

        # Verify it's alive
        result = client.call("riva/ping")
        assert result["result"] == "pong"

        yield client

        # Cleanup: the daemon thread dies with the test process
