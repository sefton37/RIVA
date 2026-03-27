"""JSON-RPC 2.0 dispatcher for the RIVA service.

Receives JSON-RPC requests, routes to handlers, returns JSON-RPC responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from riva.rpc_handlers.system import handle_ping, handle_status

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Dispatch table: method name -> handler_fn
# Handlers receive **params and return a dict.
_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "riva/ping": lambda **_kw: handle_ping(),
    "riva/status": lambda **_kw: handle_status(),
}


def register_method(
    method: str,
    handler: Callable[..., dict[str, Any]],
) -> None:
    """Register an RPC method handler.

    Args:
        method: The JSON-RPC method name (e.g. "riva/pm/epics/list").
        handler: Callable that receives **params and returns a dict.
    """
    _DISPATCH[method] = handler


def _make_response(
    id: int | str | None,
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 response envelope."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def _make_error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error object."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


def dispatch(raw: str) -> str:
    """Parse a JSON-RPC 2.0 request and dispatch to the handler.

    Args:
        raw: Raw JSON string of the request.

    Returns:
        JSON string of the response.
    """
    # Parse
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return json.dumps(
            _make_response(None, error=_make_error(PARSE_ERROR, f"Parse error: {exc}"))
        )

    # Validate structure
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if not isinstance(method, str):
        return json.dumps(
            _make_response(
                req_id, error=_make_error(INVALID_REQUEST, "Missing or invalid 'method'")
            )
        )

    if not isinstance(params, dict):
        return json.dumps(
            _make_response(
                req_id, error=_make_error(INVALID_PARAMS, "'params' must be an object")
            )
        )

    # Lookup handler
    handler = _DISPATCH.get(method)
    if handler is None:
        return json.dumps(
            _make_response(
                req_id,
                error=_make_error(METHOD_NOT_FOUND, f"Unknown method: {method}"),
            )
        )

    # Dispatch
    try:
        result = handler(**params)
        return json.dumps(_make_response(req_id, result=result))
    except TypeError as exc:
        # Wrong params for the handler
        logger.warning("Invalid params for %s: %s", method, exc)
        return json.dumps(
            _make_response(
                req_id, error=_make_error(INVALID_PARAMS, f"Invalid params: {exc}")
            )
        )
    except Exception as exc:
        logger.exception("Handler error for %s", method)
        return json.dumps(
            _make_response(
                req_id,
                error=_make_error(INTERNAL_ERROR, f"Internal error: {exc}"),
            )
        )
