"""Read-only Run Capsule integrity, signature, and drift projection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from gigaloom.review.api import (
    FilesystemRunCapsuleRepository,
    verify_run_capsule,
)
from gigaloom.ui.schemas.run_capsules import (
    CapsuleDriftEvidence,
    CapsuleDriftFinding,
    CapsuleSignatureEvidence,
    RunCapsuleWebEvidence,
)


class RunCapsuleObservedInputsProvider(Protocol):
    """Authorize a Web read and return current content-free input facts."""

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, str | None]: ...


class RunCapsuleEvidenceQuery:
    """Verify retained archives and compare only owner-supplied current facts."""

    def __init__(
        self,
        repository: FilesystemRunCapsuleRepository,
        observed_inputs: RunCapsuleObservedInputsProvider,
    ) -> None:
        self._repository = repository
        self._observed_inputs = observed_inputs

    def get(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> RunCapsuleWebEvidence:
        """Return one verified, access-bound, content-free Web projection."""
        observed = dict(
            self._observed_inputs.observed_inputs_for_run(
                run_id=run_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        )
        if len(observed) > 64:
            raise ValueError("run capsule observed input set exceeds the limit")
        record = self._repository.get_by_run(run_id)
        archive = self._repository.archive_for_run(run_id)
        report = verify_run_capsule(archive, observed_inputs=observed)
        findings = tuple(
            CapsuleDriftFinding(
                field=item.field,
                status=item.status.value,
                expected=item.expected,
                observed=item.observed,
            )
            for item in report.findings
        )
        counts = Counter(item.status for item in findings)
        if counts["drifted"]:
            drift_status = "drifted"
        elif counts["unverifiable"] or counts["omitted"]:
            drift_status = "unverifiable"
        else:
            drift_status = "current"
        return RunCapsuleWebEvidence(
            run_id=run_id,
            capsule_id=record.capsule_id,
            capsule_sha256=record.capsule_sha256,
            archive_sha256=record.archive_sha256,
            created_at=record.created_at,
            signature=CapsuleSignatureEvidence(
                status=report.signature_status.value,
                valid=report.signature_valid,
                signer_id=report.signer_id,
                trust_status=report.trust_status,
            ),
            drift=CapsuleDriftEvidence(
                status=drift_status,
                matched_count=counts["matched"],
                drifted_count=counts["drifted"],
                unverifiable_count=counts["unverifiable"],
                omitted_count=counts["omitted"],
                findings=findings,
            ),
            export_path=(
                f"/api/operator/runs/{quote(run_id, safe='')}/capsule/export"
                f"?workspace_id={quote(workspace_id, safe='')}"
            ),
        )

    def export_path(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Path:
        """Authorize and return one verified local archive for download."""
        self._observed_inputs.observed_inputs_for_run(
            run_id=run_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        return self._repository.archive_for_run(run_id)


__all__ = ["RunCapsuleEvidenceQuery", "RunCapsuleObservedInputsProvider"]
