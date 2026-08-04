"""Route-local action port shared by Thread Relay CLI and HTTP adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ThreadRelayRouteActions(Protocol):
    """Bound actor/project application actions required by route adapters."""

    def list_threads(
        self,
        *,
        source: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        """Return one bounded thread page."""

    def read_thread(
        self,
        *,
        source: str,
        thread_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        """Return one bounded visible thread projection."""

    def list_deliveries(
        self,
        *,
        source: str,
        thread_id: str,
        direction: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        """Return one content-free incoming or outgoing delivery page."""

    def preview_send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return a content-free preview without persisting or mutating."""

    def send(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
    ) -> Mapping[str, Any]:
        """Deliver only against the exact preview digest."""

    def status(self, delivery_id: str) -> Mapping[str, Any]:
        """Return one actor/project-bound digest-only delivery status."""


def validated_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a preview is digest-bound and does not echo message content."""
    payload = dict(value)
    digest = payload.get("preview_digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("thread relay preview digest is invalid")
    forbidden = {"content", "text", "prompt", "message"}
    if _mapping_keys(payload) & forbidden:
        raise ValueError("thread relay preview must not echo message content")
    return payload


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key).lower() for key in value}
        for item in value.values():
            keys.update(_mapping_keys(item))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys.update(_mapping_keys(item))
        return keys
    return set()


__all__ = ["ThreadRelayRouteActions", "validated_preview"]
