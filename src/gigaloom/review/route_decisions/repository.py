"""Append-only local repository for immutable route decision receipts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from gigaloom.review.route_decisions.codec import (
    canonical_route_decision_bytes,
    route_decision_receipt_from_dict,
)
from gigaloom.review.route_decisions.models import (
    MAX_ROUTE_DECISION_BYTES,
    RouteDecisionConflictError,
    RouteDecisionError,
    RouteDecisionNotFoundError,
    RouteDecisionReceiptV1,
    _validate_decision_id,
)


class RouteDecisionRepository:
    """Persist content-free receipts without overwriting existing decisions."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, receipt: RouteDecisionReceiptV1) -> RouteDecisionReceiptV1:
        """Atomically create or idempotently confirm one immutable receipt."""
        encoded = canonical_route_decision_bytes(receipt)
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self._path(receipt.route_decision_id)
        if destination.exists():
            return self._confirm_existing(destination, receipt)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".route-decision-",
                dir=self._root,
                delete=False,
            ) as handle:
                temporary_name = handle.name
                os.chmod(handle.name, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_name, destination)
            except FileExistsError:
                return self._confirm_existing(destination, receipt)
            self._fsync_root()
            return receipt
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def get(self, route_decision_id: str) -> RouteDecisionReceiptV1:
        """Load and verify one receipt by strict content-derived id."""
        path = self._path(route_decision_id)
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise RouteDecisionNotFoundError(
                f"route decision not found: {route_decision_id}"
            ) from exc
        if size > MAX_ROUTE_DECISION_BYTES:
            raise RouteDecisionError("stored route decision exceeds size limit")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RouteDecisionError("stored route decision is unreadable") from exc
        receipt = route_decision_receipt_from_dict(payload)
        if receipt.route_decision_id != route_decision_id:
            raise RouteDecisionError("stored route decision id mismatch")
        return receipt

    def _path(self, route_decision_id: str) -> Path:
        _validate_decision_id(route_decision_id)
        return self._root / f"{route_decision_id}.json"

    def _confirm_existing(
        self,
        destination: Path,
        receipt: RouteDecisionReceiptV1,
    ) -> RouteDecisionReceiptV1:
        existing = self.get(receipt.route_decision_id)
        if existing != receipt:
            raise RouteDecisionConflictError(
                "route decision id already has different immutable content"
            )
        return existing

    def _fsync_root(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["RouteDecisionRepository"]
