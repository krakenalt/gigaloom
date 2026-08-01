"""Immutable content-free headless result and terminal receipts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessExitCode,
    HeadlessTerminalReceiptV1,
    headless_terminal_receipt_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_json_bytes
from gigaloom.execution.headless.contracts import (
    HeadlessExecutionResult,
    PreparedHeadlessRun,
)


HEADLESS_RESULT_REF = "headless-result.json"
HEADLESS_TERMINAL_RECEIPT_REF = "terminal-receipt.json"
HEADLESS_PARTIAL_RECEIPT_REF = "partial-stream-receipt.json"
HEADLESS_BACKEND_RESULT_REF = "backend-result.json"
MAX_HEADLESS_RESULT_BYTES = 64 * 1024


class HeadlessResultStoreError(RuntimeError):
    """Content-free immutable result-store failure."""


class HeadlessResultStore:
    """Publish bounded immutable artifacts inside one admitted result root."""

    def __init__(
        self,
        result_dir: str,
        *,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(result_dir).resolve(strict=True)
        if not self.root.is_dir():
            raise HeadlessResultStoreError("headless result root is unavailable")
        self._id_factory = id_factory or (
            lambda prefix: f"{prefix}-{secrets.token_hex(12)}"
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def validate_backend_artifacts(
        self,
        result: HeadlessExecutionResult,
    ) -> tuple[str, ...]:
        """Return omission codes for missing or redirected backend artifacts."""
        omissions: list[str] = []
        for reference, code in (
            (result.result_ref, "backend_result_artifact_missing"),
            (result.capsule_ref, "backend_capsule_artifact_missing"),
        ):
            if reference is None:
                continue
            reference_path = self.root / reference
            candidate = reference_path.resolve(strict=False)
            if (
                not candidate.is_relative_to(self.root)
                or not candidate.is_file()
                or reference_path.is_symlink()
            ):
                omissions.append(code)
        return tuple(omissions)

    def write_result(
        self,
        prepared: PreparedHeadlessRun,
        result: HeadlessExecutionResult,
    ) -> str:
        """Persist a prompt-free terminal summary."""
        payload = {
            "schema_version": 1,
            "run_id": prepared.invocation.run_id,
            "agent_id": prepared.invocation.agent_id,
            "route_id": prepared.invocation.route_id,
            "model_id": prepared.invocation.model_id,
            "route_observation_digest": prepared.route.observation_digest,
            "status": result.status.value,
            "exit_code": int(result.status.exit_code),
            "backend_result_ref": result.result_ref,
            "backend_capsule_ref": result.capsule_ref,
            "omissions": list(result.omissions),
            "diagnostic_code": result.diagnostic_code,
            "content_free": True,
        }
        self._publish(HEADLESS_RESULT_REF, canonical_json_bytes(payload))
        return HEADLESS_RESULT_REF

    def write_backend_result(
        self,
        *,
        run_id: str,
        agent_id: str,
        route_id: str,
        stop_reason: str,
        capability_snapshot_digest: str,
        usage: Mapping[str, object] | None,
    ) -> str:
        """Publish one bounded prompt-free backend completion projection."""
        document = {
            "schema_version": 1,
            "run_id": run_id,
            "agent_id": agent_id,
            "route_id": route_id,
            "stop_reason": stop_reason,
            "capability_snapshot_digest": capability_snapshot_digest,
            "usage": dict(usage) if usage is not None else None,
            "prompt_captured": False,
            "content_free": True,
        }
        self._publish(HEADLESS_BACKEND_RESULT_REF, canonical_json_bytes(document))
        return HEADLESS_BACKEND_RESULT_REF

    def write_terminal_receipt(
        self,
        receipt: HeadlessTerminalReceiptV1,
    ) -> str:
        """Persist normal terminal evidence exactly once."""
        self._publish(
            HEADLESS_TERMINAL_RECEIPT_REF,
            canonical_json_bytes(headless_terminal_receipt_to_dict(receipt)),
        )
        return HEADLESS_TERMINAL_RECEIPT_REF

    def write_partial_stream_receipt(
        self,
        *,
        prepared: PreparedHeadlessRun,
        final_sequence: int,
    ) -> HeadlessTerminalReceiptV1:
        """Persist recovery evidence when stdout cannot reach terminal framing."""
        receipt = HeadlessTerminalReceiptV1(
            receipt_id=self._id_factory("headless-terminal"),
            run_id=prepared.invocation.run_id,
            terminal_kind=HeadlessEventKind.RUN_FAILED,
            final_sequence=max(final_sequence, 0),
            exit_code=HeadlessExitCode.STATE_OR_INTEGRITY_FAILURE,
            result_ref=HEADLESS_PARTIAL_RECEIPT_REF,
            capsule_ref=None,
            omissions=(
                "stdout_stream_incomplete",
                "stdout_terminal_event_missing",
            ),
            finished_at=self._clock(),
        )
        self._publish(
            HEADLESS_PARTIAL_RECEIPT_REF,
            canonical_json_bytes(headless_terminal_receipt_to_dict(receipt)),
        )
        return receipt

    def terminal_receipt(
        self,
        *,
        prepared: PreparedHeadlessRun,
        result: HeadlessExecutionResult,
        final_sequence: int,
        result_ref: str,
    ) -> HeadlessTerminalReceiptV1:
        """Build the frozen receipt matching one planned terminal event."""
        return HeadlessTerminalReceiptV1(
            receipt_id=self._id_factory("headless-terminal"),
            run_id=prepared.invocation.run_id,
            terminal_kind=result.status.terminal_kind,
            final_sequence=final_sequence,
            exit_code=result.status.exit_code,
            result_ref=result_ref,
            capsule_ref=result.capsule_ref,
            omissions=result.omissions,
            finished_at=self._clock(),
        )

    def _publish(self, relative_path: str, payload: bytes) -> None:
        if not payload or len(payload) > MAX_HEADLESS_RESULT_BYTES:
            raise HeadlessResultStoreError("headless result artifact violates bounds")
        target = self.root / relative_path
        temp = self.root / f".{relative_path}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, target)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (FileExistsError, OSError) as error:
            raise HeadlessResultStoreError(
                "headless result artifact publication failed"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "HEADLESS_BACKEND_RESULT_REF",
    "HEADLESS_PARTIAL_RECEIPT_REF",
    "HEADLESS_RESULT_REF",
    "HEADLESS_TERMINAL_RECEIPT_REF",
    "HeadlessResultStore",
    "HeadlessResultStoreError",
]
