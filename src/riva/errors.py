"""RIVA-specific error types.

All inherit from trcore's TalkingRockError for consistent RPC error handling.
"""

from __future__ import annotations

from trcore.errors import TalkingRockError


class RivaError(TalkingRockError):
    """Base class for all RIVA errors."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(message, **kwargs)


class EntryGuardBlocked(RivaError):
    """Entry guard rejected the message (safety or intent)."""

    def __init__(self, reason: str, guard_type: str) -> None:
        super().__init__(
            f"Entry guard ({guard_type}) blocked: {reason}",
            recoverable=True,
            context={"guard_type": guard_type, "reason": reason},
        )
        self.guard_type = guard_type
        self.reason = reason


class PlanError(RivaError):
    """Error during plan generation or management."""


class ContractError(RivaError):
    """Error during contract lifecycle."""


class AuditError(RivaError):
    """Error during audit execution."""


class PropertiesError(RivaError):
    """Error during properties sync or management."""

    def __init__(self, message: str, *, conflict: bool = False, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.conflict = conflict


class PmError(RivaError):
    """Error during PM table operations (not found, validation)."""


class ServiceError(RivaError):
    """Error in the RIVA service itself (socket, lifecycle)."""
