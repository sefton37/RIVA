"""Tests for the RIVA RPC dispatcher.

Covers:
- Unknown method returns -32601
- Valid method dispatches correctly
- Parse errors handled
- Invalid request structure handled
- Entry guard called before guarded methods
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from riva.rpc_dispatcher import (
    ENTRY_GUARD_BLOCKED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    dispatch,
    register_method,
)


def _make_request(method: str, params: dict | None = None, id: int = 1) -> str:
    """Helper to build a JSON-RPC request string."""
    req = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        req["params"] = params
    return json.dumps(req)


class TestDispatchBasics:
    """Basic dispatch tests."""

    def test_ping(self):
        """riva/ping returns pong."""
        response = json.loads(dispatch(_make_request("riva/ping")))
        assert response["result"]["result"] == "pong"
        assert response["id"] == 1

    def test_status(self):
        """riva/status returns running status."""
        response = json.loads(dispatch(_make_request("riva/status")))
        assert response["result"]["status"] == "running"
        assert "uptime_seconds" in response["result"]
        assert "version" in response["result"]

    def test_unknown_method(self):
        """Unknown method returns METHOD_NOT_FOUND (-32601)."""
        response = json.loads(dispatch(_make_request("riva/nonexistent")))
        assert response["error"]["code"] == METHOD_NOT_FOUND

    def test_parse_error(self):
        """Malformed JSON returns PARSE_ERROR (-32700)."""
        response = json.loads(dispatch("{not valid json"))
        assert response["error"]["code"] == PARSE_ERROR

    def test_missing_method(self):
        """Request without 'method' returns INVALID_REQUEST."""
        response = json.loads(dispatch(json.dumps({"jsonrpc": "2.0", "id": 1})))
        assert response["error"]["code"] == INVALID_REQUEST

    def test_invalid_params_type(self):
        """Non-object params returns INVALID_PARAMS."""
        req = json.dumps({
            "jsonrpc": "2.0",
            "method": "riva/ping",
            "params": "not an object",
            "id": 1,
        })
        response = json.loads(dispatch(req))
        assert response["error"]["code"] == INVALID_PARAMS


class TestMethodRegistration:
    """Tests for dynamic method registration."""

    def test_register_and_call(self):
        """Registered methods can be called."""
        register_method(
            "riva/test/echo",
            lambda message="", **_kw: {"echo": message},
        )

        response = json.loads(
            dispatch(_make_request("riva/test/echo", {"message": "hello"}))
        )
        assert response["result"]["echo"] == "hello"

    def test_handler_exception(self):
        """Handler exception returns INTERNAL_ERROR."""
        register_method(
            "riva/test/fail",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        # Actually, a generator expression won't raise on call.
        # Use a proper function.
        def _explode(**_kw):
            raise RuntimeError("boom")

        register_method("riva/test/fail", _explode)

        response = json.loads(dispatch(_make_request("riva/test/fail")))
        assert response["error"]["code"] == INTERNAL_ERROR


class TestEntryGuard:
    """Tests for entry guard integration in the dispatcher."""

    def setup_method(self):
        """Register a guarded test method."""
        register_method(
            "riva/test/guarded",
            lambda user_request="", **_kw: {"processed": user_request},
            guarded=True,
        )

    @patch("riva.rpc_dispatcher.guard_or_raise")
    def test_guard_called_for_guarded_method(self, mock_guard):
        """Entry guard is called before guarded methods."""
        mock_guard.return_value = None  # pass through
        provider = MagicMock()

        response = json.loads(
            dispatch(
                _make_request("riva/test/guarded", {"user_request": "do something"}),
                provider=provider,
            )
        )

        mock_guard.assert_called_once_with(provider, "do something")
        assert response["result"]["processed"] == "do something"

    @patch("riva.rpc_dispatcher.guard_or_raise")
    def test_guard_blocks_returns_error(self, mock_guard):
        """Entry guard blocking returns ENTRY_GUARD_BLOCKED error."""
        from riva.errors import EntryGuardBlocked

        mock_guard.side_effect = EntryGuardBlocked(
            reason="unsafe", guard_type="safety"
        )
        provider = MagicMock()

        response = json.loads(
            dispatch(
                _make_request("riva/test/guarded", {"user_request": "delete everything"}),
                provider=provider,
            )
        )

        assert response["error"]["code"] == ENTRY_GUARD_BLOCKED

    def test_guard_skipped_without_provider(self):
        """Guard is skipped when no provider is available."""
        # No provider -> guard not called, method proceeds
        response = json.loads(
            dispatch(
                _make_request("riva/test/guarded", {"user_request": "test"})
            )
        )

        assert response["result"]["processed"] == "test"

    @patch("riva.rpc_dispatcher.guard_or_raise")
    def test_guard_not_called_for_unguarded(self, mock_guard):
        """Entry guard is NOT called for unguarded methods like ping."""
        provider = MagicMock()

        dispatch(_make_request("riva/ping"), provider=provider)

        mock_guard.assert_not_called()
