"""Transport-neutral Run Capsule capture composition."""

from __future__ import annotations

from .artifacts import build_run_capsule, unsigned_signature_metadata
from .models import (
    ArtifactManifest,
    InputLock,
    OmissionManifest,
    OutputReceipt,
    RunCapsuleBundle,
)
from .signatures import (
    CapsuleSigner,
    SIGNED_MEMBER_PATHS,
    build_signature_material,
    signed_metadata,
)


def capture_run_capsule(
    *,
    input_lock: InputLock,
    output_receipt: OutputReceipt,
    artifacts: ArtifactManifest,
    omissions: OmissionManifest,
    created_at: str,
    signer: CapsuleSigner | None = None,
) -> RunCapsuleBundle:
    """Compose immutable facts without importing or invoking a live runner."""
    signature_metadata = (
        unsigned_signature_metadata() if signer is None else signed_metadata(signer)
    )
    capsule = build_run_capsule(
        created_at=created_at,
        input_lock_sha256=input_lock.sha256,
        output_receipt_sha256=output_receipt.sha256,
        artifacts_sha256=artifacts.sha256,
        omissions_sha256=omissions.sha256,
        signature=signature_metadata,
    )
    content_files = {
        "artifacts.json": artifacts._canonical,
        "capsule.json": capsule._canonical,
        "input-lock.json": input_lock._canonical,
        "omissions.json": omissions._canonical,
        "output-receipt.json": output_receipt._canonical,
    }
    assert tuple(sorted(content_files)) == SIGNED_MEMBER_PATHS
    signature_manifest, signature, public_key_document = build_signature_material(
        capsule=capsule,
        content_files=content_files,
        created_at=created_at,
        signer=signer,
    )
    return RunCapsuleBundle(
        capsule=capsule,
        input_lock=input_lock,
        output_receipt=output_receipt,
        artifacts=artifacts,
        omissions=omissions,
        signature_manifest=signature_manifest,
        signature=signature,
        public_key_document=public_key_document,
    )
