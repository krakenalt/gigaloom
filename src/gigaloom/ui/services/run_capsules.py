"""Read-only Run Capsule integrity, signature, and drift projection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import quote

from gigaloom.review.api import (
    CapsuleError,
    FilesystemRunCapsuleRepository,
    LANE_DELTA_METADATA_KEY,
    LANE_DELTA_REFERENCE_KIND,
    FilesystemLaneDeltaPacketStore,
    LaneDeltaLifecycleError,
    LaneDeltaStorageError,
    planned_changed_selectors,
    verify_run_capsule,
)
from gigaloom.sessions import HarnessSessionStore
from gigaloom.review.workspace.api import EvidenceWorkspaceProjection
from gigaloom.ui.schemas.run_capsules import (
    CapsuleDriftEvidence,
    CapsuleDriftFinding,
    CapsuleLaneDeltaReference,
    CapsuleRunReference,
    CapsuleSignatureEvidence,
    RunCapsuleWebEvidence,
)
from gigaloom.ui.services.operator_workspace import OperatorEvidenceQuery


class RunCapsuleObservedInputsProvider(Protocol):
    """Authorize a Web read and return current content-free input facts."""

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, str | None]: ...


class RunCapsuleLaneReferenceProvider(Protocol):
    """Load verified lane-delta references for one retained run."""

    def references_for_run(
        self,
        run_id: str,
    ) -> tuple[CapsuleLaneDeltaReference, ...]: ...


class RunCapsuleEvidenceQuery:
    """Verify retained archives and compare only owner-supplied current facts."""

    def __init__(
        self,
        repository: FilesystemRunCapsuleRepository,
        observed_inputs: RunCapsuleObservedInputsProvider,
        lane_references: RunCapsuleLaneReferenceProvider | None = None,
    ) -> None:
        self._repository = repository
        self._observed_inputs = observed_inputs
        self._lane_references = lane_references

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
        references = (
            self._lane_references.references_for_run(run_id)
            if self._lane_references is not None
            else ()
        )
        _verify_capsule_reference_bindings(
            references,
            capsule_sha256=record.capsule_sha256,
        )
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
        elif findings:
            drift_status = "current"
        else:
            drift_status = "unverifiable"
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
            references=references,
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


class SessionLaneDeltaReferenceProvider:
    """Revalidate a run-owned lane reference against immutable packet bytes."""

    def __init__(
        self,
        session_store: HarnessSessionStore,
        packet_store: FilesystemLaneDeltaPacketStore,
    ) -> None:
        self._session_store = session_store
        self._packet_store = packet_store

    def references_for_run(
        self,
        run_id: str,
    ) -> tuple[CapsuleLaneDeltaReference, ...]:
        """Return at most one exact lane packet reference for a run."""
        try:
            run = self._session_store.get_run(run_id)
        except KeyError:
            return ()
        reference = run.metadata.get(LANE_DELTA_METADATA_KEY)
        if reference is None:
            return ()
        if not isinstance(reference, Mapping):
            raise CapsuleError("lane delta run reference is invalid")
        expected_fields = {
            "schema_version",
            "kind",
            "packet_id",
            "packet_sha256",
            "size_bytes",
            "source_lane_sha256",
            "destination_lane_sha256",
            "changed_selectors",
            "changed_anchor_reason_codes",
            "run_capsule_references",
            "disclosure_mode",
            "content_mode",
            "content_free",
            "hidden_state_portability_claimed",
            "omissions",
        }
        if (
            set(reference) != expected_fields
            or type(reference.get("schema_version")) is not int
            or reference.get("schema_version") != 1
            or reference.get("kind") != LANE_DELTA_REFERENCE_KIND
        ):
            raise CapsuleError("lane delta run reference fields are invalid")
        try:
            stored = self._packet_store.load_by_digests(
                _required_text(reference.get("packet_id"), "packet_id"),
                expected_source_lane_digest=_required_text(
                    reference.get("source_lane_sha256"),
                    "source_lane_sha256",
                ),
                expected_destination_lane_digest=_required_text(
                    reference.get("destination_lane_sha256"),
                    "destination_lane_sha256",
                ),
            )
        except (KeyError, LaneDeltaStorageError, ValueError) as error:
            raise CapsuleError("lane delta packet reference did not verify") from error
        packet = stored.packet
        capsule_references = _capsule_references(
            reference,
            source_sha256=packet.source_lane.run_capsule_digest,
            destination_sha256=packet.destination_lane.run_capsule_digest,
        )
        changed_anchor_codes = tuple(
            item.reason_code for item in packet.changed_anchors
        )
        try:
            changed_selectors = planned_changed_selectors(
                run,
                source_lane=packet.source_lane,
                destination_lane=packet.destination_lane,
            )
        except LaneDeltaLifecycleError as error:
            raise CapsuleError("lane delta run plan did not verify") from error
        if (
            reference.get("packet_sha256") != stored.packet_sha256
            or reference.get("size_bytes") != stored.size_bytes
            or _string_tuple(
                reference.get("changed_selectors"),
                "changed_selectors",
            )
            != changed_selectors
            or _string_tuple(
                reference.get("changed_anchor_reason_codes"),
                "changed_anchor_reason_codes",
            )
            != changed_anchor_codes
            or reference.get("disclosure_mode") != packet.disclosure_mode.value
            or reference.get("content_mode") != packet.content_mode.value
            or reference.get("content_free") is not True
            or reference.get("hidden_state_portability_claimed") is not False
            or _string_tuple(reference.get("omissions"), "omissions")
            != packet.omissions
        ):
            raise CapsuleError("lane delta run reference changed after capture")
        return (
            CapsuleLaneDeltaReference(
                kind="gigaloom.lane_delta.reference.v1",
                packet_id=packet.packet_id,
                packet_sha256=stored.packet_sha256,
                size_bytes=stored.size_bytes,
                source_lane_sha256=packet.source_lane.lane_digest,
                destination_lane_sha256=packet.destination_lane.lane_digest,
                changed_selectors=changed_selectors,
                changed_anchor_reason_codes=changed_anchor_codes,
                run_capsule_references=capsule_references,
                disclosure_mode="packet",
                content_mode="content_free",
                content_free=True,
                hidden_state_portability_claimed=False,
                omissions=packet.omissions,
            ),
        )


class OperatorEvidenceObservedInputsProvider:
    """Reuse the owner-bound evidence authority before capsule inspection."""

    def __init__(self, evidence: OperatorEvidenceQuery | None) -> None:
        self._evidence = evidence

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, str | None]:
        """Authorize access and keep unsupported current facts unverifiable."""
        if self._evidence is None:
            raise PermissionError("run evidence authority is unavailable")
        projection = self._evidence.get_evidence_workspace(
            run_id=run_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        checked = EvidenceWorkspaceProjection.from_dict(projection.to_dict())
        if (
            checked.run.run_id != run_id
            or checked.run.owner_id != owner_id
            or checked.run.workspace_id != workspace_id
        ):
            raise PermissionError("run evidence binding does not match")
        return {}


__all__ = [
    "OperatorEvidenceObservedInputsProvider",
    "RunCapsuleEvidenceQuery",
    "RunCapsuleLaneReferenceProvider",
    "RunCapsuleObservedInputsProvider",
    "SessionLaneDeltaReferenceProvider",
]


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapsuleError(f"lane delta {field_name} is invalid")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CapsuleError(f"lane delta {field_name} is invalid")
    return tuple(value)


def _capsule_references(
    reference: Mapping[str, object],
    *,
    source_sha256: str,
    destination_sha256: str,
) -> tuple[CapsuleRunReference, CapsuleRunReference]:
    values = reference.get("run_capsule_references")
    if not isinstance(values, list) or len(values) != 2:
        raise CapsuleError("lane delta Run Capsule references are invalid")
    expected = (
        ("source", source_sha256),
        ("destination", destination_sha256),
    )
    projected: list[CapsuleRunReference] = []
    for value, (role, sha256) in zip(values, expected, strict=True):
        if (
            not isinstance(value, Mapping)
            or set(value) != {"role", "sha256", "status"}
            or value.get("role") != role
            or value.get("sha256") != sha256
            or value.get("status") not in {"captured", "not_captured"}
        ):
            raise CapsuleError("lane delta Run Capsule reference is invalid")
        status = cast(Literal["captured", "not_captured"], value["status"])
        projected.append(
            CapsuleRunReference(
                role=cast(Literal["source", "destination"], role),
                sha256=sha256,
                status=status,
            )
        )
    return projected[0], projected[1]


def _verify_capsule_reference_bindings(
    references: tuple[CapsuleLaneDeltaReference, ...],
    *,
    capsule_sha256: str,
) -> None:
    for reference in references:
        destination = reference.run_capsule_references[1]
        if (
            destination.role != "destination"
            or destination.status != "captured"
            or destination.sha256 != capsule_sha256
        ):
            raise CapsuleError("lane delta destination capsule binding changed")
