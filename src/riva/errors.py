"""RIVA-specific error types.

All inherit from cairn's TalkingRockError for consistent RPC error handling.
"""

from __future__ import annotations

from cairn.errors import TalkingRockError


class RivaError(TalkingRockError):
    """Base class for all RIVA errors."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(message, **kwargs)


class PmError(RivaError):
    """Error during PM table operations (not found, validation)."""


class ServiceError(RivaError):
    """Error in the RIVA service itself (socket, lifecycle)."""
