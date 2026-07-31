"""Run-completion lifecycle binding for content-free capsule capture."""

from __future__ import annotations

from gigaloom.execution.api import (
    RunCompletionArtifactV1,
    RunCompletionHook,
)

from .ports import RunCapsuleCapturePortsV1, capture_run_capsule_from_ports
from .repository import FilesystemRunCapsuleRepository
from .signatures import CapsuleSigner


class RunCapsuleLifecycleService(RunCompletionHook):
    """Capture one immutable capsule after terminal run persistence."""

    def __init__(
        self,
        *,
        ports: RunCapsuleCapturePortsV1,
        repository: FilesystemRunCapsuleRepository,
        signer: CapsuleSigner | None = None,
    ) -> None:
        self._ports = ports
        self._repository = repository
        self._signer = signer

    def on_run_completed(
        self,
        run_id: str,
        *,
        completed_at: str,
    ) -> tuple[RunCompletionArtifactV1, ...]:
        """Capture, retain, and project one content-free capsule binding."""
        bundle = capture_run_capsule_from_ports(
            run_id,
            self._ports,
            created_at=completed_at,
            signer=self._signer,
        )
        record = self._repository.save(run_id, bundle, created_at=completed_at)
        return (
            RunCompletionArtifactV1(
                kind="run_capsule",
                artifact_id=record.capsule_id,
                sha256=record.capsule_sha256,
                status="captured",
                attributes={
                    "archive_sha256": record.archive_sha256,
                    "content_free": True,
                    "signature_status": record.signature_status.value,
                    "signer_id": record.signer_id,
                    "trust_status": record.trust_status,
                },
            ),
        )


__all__ = ["RunCapsuleLifecycleService"]
