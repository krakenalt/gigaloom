"""Instance-bound JSON-RPC validation for the MCP App postMessage bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
from threading import RLock
from typing import Callable, Mapping

from .contracts import (
    MCPAppBridgeRequest,
    MCPAppFrameBinding,
    MCPAppLimits,
)
from .errors import MCPAppChannelError
from .evidence import MCPAppSecurityEvent

MCP_APP_ALLOWED_METHODS = frozenset({"ui/ready", "ui/userChoice"})


class MCPAppBridge:
    """Validate one frame's messages and track replay/outstanding state."""

    def __init__(
        self,
        *,
        instance_id: str,
        source_id: str,
        channel_id: str,
        nonce: str,
        binding: MCPAppFrameBinding,
        limits: MCPAppLimits,
        record_event: Callable[[MCPAppSecurityEvent], None] | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.source_id = source_id
        self.channel_id = channel_id
        self.nonce = nonce
        self.binding = binding
        self._limits = limits
        self._record_event = record_event
        self._outstanding: set[str | int] = set()
        self._seen: set[str | int] = set()
        self._closed = False
        self._lock = RLock()

    def accept(self, *, source_id: str, raw_message: bytes) -> MCPAppBridgeRequest:
        """Validate and register one app-to-host JSON-RPC request."""
        with self._lock:
            try:
                request = self._accept_locked(
                    source_id=source_id, raw_message=raw_message
                )
            except MCPAppChannelError as exc:
                self._audit(exc.code)
                raise
            self._audit(
                "request_accepted",
                method=request.method,
                request_id=request.request_id,
            )
            return request

    def complete(self, request_id: str | int) -> bool:
        """Complete an outstanding request; ignore audited late responses."""
        with self._lock:
            if self._closed or request_id not in self._outstanding:
                self._audit("late_response", request_id=request_id)
                return False
            self._outstanding.remove(request_id)
            self._audit("request_completed", request_id=request_id)
            return True

    def teardown(self) -> tuple[str | int, ...]:
        """Close the frame and cancel all pending request ids."""
        with self._lock:
            cancelled = tuple(sorted(self._outstanding, key=str))
            self._outstanding.clear()
            self._closed = True
            self._audit("frame_destroyed")
            return cancelled

    def _accept_locked(
        self,
        *,
        source_id: str,
        raw_message: bytes,
    ) -> MCPAppBridgeRequest:
        if self._closed:
            raise MCPAppChannelError("frame_closed", "MCP App frame is closed")
        if not hmac.compare_digest(source_id, self.source_id):
            raise MCPAppChannelError(
                "source_mismatch", "MCP App message source does not match its frame"
            )
        if not raw_message or len(raw_message) > self._limits.max_post_message_bytes:
            raise MCPAppChannelError(
                "payload_limit", "MCP App message exceeds the configured byte limit"
            )
        try:
            payload = json.loads(raw_message)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPAppChannelError(
                "invalid_json", "MCP App message is not valid JSON"
            ) from exc
        if not isinstance(payload, dict) or _json_depth(payload) > 32:
            raise MCPAppChannelError(
                "invalid_jsonrpc", "MCP App message must be a bounded JSON object"
            )
        allowed_keys = {"jsonrpc", "id", "method", "params", "channelId", "nonce"}
        if set(payload) - allowed_keys or payload.get("jsonrpc") != "2.0":
            raise MCPAppChannelError(
                "invalid_jsonrpc", "MCP App message has an invalid JSON-RPC envelope"
            )
        if not _constant_time_text(payload.get("channelId"), self.channel_id):
            raise MCPAppChannelError(
                "channel_mismatch", "MCP App message channel does not match its frame"
            )
        if not _constant_time_text(payload.get("nonce"), self.nonce):
            raise MCPAppChannelError(
                "nonce_mismatch", "MCP App message nonce does not match its frame"
            )
        request_id = payload.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise MCPAppChannelError(
                "invalid_request_id", "MCP App request id must be a string or integer"
            )
        if isinstance(request_id, str) and (not request_id or len(request_id) > 128):
            raise MCPAppChannelError(
                "invalid_request_id", "MCP App string request id is not bounded"
            )
        if request_id in self._seen:
            raise MCPAppChannelError(
                "replayed_request", "MCP App request id has already been used"
            )
        method = payload.get("method")
        if not isinstance(method, str) or method not in MCP_APP_ALLOWED_METHODS:
            raise MCPAppChannelError(
                "method_denied", "MCP App method is not allowed by host policy"
            )
        params = payload.get("params", {})
        if not isinstance(params, dict):
            raise MCPAppChannelError(
                "invalid_params", "MCP App JSON-RPC params must be an object"
            )
        _validate_method_params(method, params)
        if len(self._outstanding) >= self._limits.max_outstanding_app_requests:
            raise MCPAppChannelError(
                "outstanding_limit",
                "MCP App outstanding request limit reached",
            )
        self._seen.add(request_id)
        self._outstanding.add(request_id)
        return MCPAppBridgeRequest(
            request_id=request_id,
            method=method,
            params=params,
            binding=self.binding,
        )

    def _audit(
        self,
        code: str,
        *,
        method: str | None = None,
        request_id: str | int | None = None,
    ) -> None:
        if self._record_event is None:
            return
        self._record_event(
            MCPAppSecurityEvent(
                instance_id=self.instance_id,
                server_id=self.binding.server_id,
                resource_sha256=self.binding.resource_sha256,
                code=code,
                method=method,
                request_id_digest=_request_id_digest(request_id),
            )
        )


def _constant_time_text(value: object, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)


def _request_id_digest(request_id: str | int | None) -> str | None:
    if request_id is None:
        return None
    typed_value = f"{type(request_id).__name__}:{request_id}".encode()
    return hashlib.sha256(typed_value).hexdigest()


def _json_depth(value: object, *, depth: int = 0) -> int:
    if depth > 32:
        return depth
    if isinstance(value, Mapping):
        return max(
            (_json_depth(item, depth=depth + 1) for item in value.values()),
            default=depth,
        )
    if isinstance(value, list):
        return max(
            (_json_depth(item, depth=depth + 1) for item in value), default=depth
        )
    return depth


def _validate_method_params(method: str, params: Mapping[str, object]) -> None:
    if method == "ui/ready" and params:
        raise MCPAppChannelError(
            "invalid_params", "MCP App ready request does not accept parameters"
        )
    if method != "ui/userChoice":
        return
    choice_id = params.get("choiceId")
    if set(params) != {"choiceId"} or not isinstance(choice_id, str):
        raise MCPAppChannelError(
            "invalid_params", "MCP App user choice requires one typed choiceId"
        )
    if not choice_id or len(choice_id.encode("utf-8")) > 256:
        raise MCPAppChannelError(
            "invalid_params", "MCP App choiceId is empty or exceeds its byte limit"
        )
