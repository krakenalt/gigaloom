"""Execution-neutral supplier ports for deferred Run Capsule capture."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from .capture import capture_run_capsule
from .models import (
    ArtifactManifest,
    InputLock,
    OmissionManifest,
    OutputReceipt,
    RunCapsuleBundle,
)
from .signatures import CapsuleSigner


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class RunCapsuleInputPort(Protocol):
    """Supply the immutable input lock captured before execution."""

    def input_lock_for_run(self, run_id: str) -> InputLock: ...


class RunCapsuleOutputPort(Protocol):
    """Supply the immutable output receipt captured after execution."""

    def output_receipt_for_run(self, run_id: str) -> OutputReceipt: ...


class RunCapsuleEvidencePort(Protocol):
    """Supply bounded retained-artifact and omission manifests."""

    def artifact_manifest_for_run(self, run_id: str) -> ArtifactManifest: ...

    def omission_manifest_for_run(self, run_id: str) -> OmissionManifest: ...


@dataclass(frozen=True)
class RunCapsuleCapturePortsV1:
    """Injected suppliers required to compose one capsule without a runner import."""

    inputs: RunCapsuleInputPort
    outputs: RunCapsuleOutputPort
    evidence: RunCapsuleEvidencePort


def capture_run_capsule_from_ports(
    run_id: str,
    ports: RunCapsuleCapturePortsV1,
    *,
    created_at: str,
    signer: CapsuleSigner | None = None,
) -> RunCapsuleBundle:
    """Read every immutable fact once and delegate to canonical capture."""
    if not isinstance(run_id, str) or _IDENTITY_RE.fullmatch(run_id) is None:
        raise ValueError("run id is invalid")
    if not isinstance(ports, RunCapsuleCapturePortsV1):
        raise ValueError("run capsule capture ports are invalid")
    input_lock = ports.inputs.input_lock_for_run(run_id)
    output_receipt = ports.outputs.output_receipt_for_run(run_id)
    artifacts = ports.evidence.artifact_manifest_for_run(run_id)
    omissions = ports.evidence.omission_manifest_for_run(run_id)
    for value, expected, field_name in (
        (input_lock, InputLock, "input lock"),
        (output_receipt, OutputReceipt, "output receipt"),
        (artifacts, ArtifactManifest, "artifact manifest"),
        (omissions, OmissionManifest, "omission manifest"),
    ):
        if not isinstance(value, expected):
            raise ValueError(f"run capsule {field_name} port returned invalid data")
    return capture_run_capsule(
        input_lock=input_lock,
        output_receipt=output_receipt,
        artifacts=artifacts,
        omissions=omissions,
        created_at=created_at,
        signer=signer,
    )


__all__ = [
    "RunCapsuleCapturePortsV1",
    "RunCapsuleEvidencePort",
    "RunCapsuleInputPort",
    "RunCapsuleOutputPort",
    "capture_run_capsule_from_ports",
]
