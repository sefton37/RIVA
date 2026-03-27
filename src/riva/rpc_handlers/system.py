"""System RPC handlers: ping, status.

These are the most basic health-check endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Service start time, set when the service boots.
_start_time: float = 0.0


def set_start_time() -> None:
    """Record the service start time. Called once at boot."""
    global _start_time
    _start_time = time.time()


def handle_ping() -> dict[str, Any]:
    """Health check. Returns pong."""
    return {"result": "pong"}


def handle_status() -> dict[str, Any]:
    """Service status with uptime."""
    uptime = time.time() - _start_time if _start_time > 0 else 0
    return {
        "status": "running",
        "uptime_seconds": round(uptime, 1),
        "version": "0.2.0",
    }
