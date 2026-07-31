"""Artifact, omission, and root capsule manifest contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .canonical import (
    canonical_json_bytes,
    canonical_sha256,
    require_exact_fields,
    require_identity,
    require_list,
    require_mapping,
    require_non_negative_int,
    require_optional_sha256,
    require_sha256,
    require_string,
    validate_content_free,
)
from .errors import CapsuleIntegrityError, CapsuleSchemaError
from .models import (
    ARTIFACT_MANIFEST_KIND,
    OMISSION_MANIFEST_KIND,
    RUN_CAPSULE_KIND,
    RUN_CAPSULE_SCHEMA_VERSION,
    ArtifactManifest,
    OmissionManifest,
    RunCapsule,
    SignatureStatus,
)


def build_artifact_manifest(entries: list[Mapping[str, Any]]) -> ArtifactManifest:
    """Build bounded content-free artifact metadata."""
    document: dict[str, Any] = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": ARTIFACT_MANIFEST_KIND,
        "content_free": True,
        "entries": [dict(entry) for entry in entries],
    }
    _validate_artifact_manifest(document, with_digest=False)
    document["artifacts_sha256"] = canonical_sha256(document)
    return ArtifactManifest(canonical_json_bytes(document))


def parse_artifact_manifest(payload: object) -> ArtifactManifest:
    document = dict(require_mapping(payload, "artifact manifest"))
    _validate_artifact_manifest(document, with_digest=True)
    _verify_digest(document, "artifacts_sha256", "artifact manifest")
    return ArtifactManifest(canonical_json_bytes(document))


def build_omission_manifest(entries: list[Mapping[str, Any]]) -> OmissionManifest:
    """Build explicit omission metadata without raw omitted values."""
    document: dict[str, Any] = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": OMISSION_MANIFEST_KIND,
        "content_free": True,
        "entries": [dict(entry) for entry in entries],
    }
    _validate_omission_manifest(document, with_digest=False)
    document["omissions_sha256"] = canonical_sha256(document)
    return OmissionManifest(canonical_json_bytes(document))


def parse_omission_manifest(payload: object) -> OmissionManifest:
    document = dict(require_mapping(payload, "omission manifest"))
    _validate_omission_manifest(document, with_digest=True)
    _verify_digest(document, "omissions_sha256", "omission manifest")
    return OmissionManifest(canonical_json_bytes(document))


def build_run_capsule(
    *,
    created_at: str,
    input_lock_sha256: str,
    output_receipt_sha256: str,
    artifacts_sha256: str,
    omissions_sha256: str,
    signature: Mapping[str, Any],
) -> RunCapsule:
    """Build the root content-addressed capsule manifest."""
    body: dict[str, Any] = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": RUN_CAPSULE_KIND,
        "content_free": True,
        "created_at": created_at,
        "input_lock_sha256": input_lock_sha256,
        "output_receipt_sha256": output_receipt_sha256,
        "artifacts_sha256": artifacts_sha256,
        "omissions_sha256": omissions_sha256,
        "signature": dict(signature),
    }
    _validate_run_capsule(body, with_identity=False)
    digest = canonical_sha256(body)
    document = {
        **body,
        "capsule_id": f"capsule_{digest[:32]}",
        "capsule_sha256": digest,
    }
    return RunCapsule(canonical_json_bytes(document))


def parse_run_capsule(payload: object) -> RunCapsule:
    document = dict(require_mapping(payload, "run capsule"))
    _validate_run_capsule(document, with_identity=True)
    digest = document["capsule_sha256"]
    if document["capsule_id"] != f"capsule_{digest[:32]}":
        raise CapsuleIntegrityError("run capsule id does not match its digest")
    body = {
        key: value
        for key, value in document.items()
        if key not in {"capsule_id", "capsule_sha256"}
    }
    if canonical_sha256(body) != digest:
        raise CapsuleIntegrityError("run capsule digest does not match")
    return RunCapsule(canonical_json_bytes(document))


def unsigned_signature_metadata() -> dict[str, None | str]:
    """Return the only honest signature projection for an unsigned capsule."""
    return {
        "status": SignatureStatus.UNSIGNED.value,
        "algorithm": None,
        "signer_id": None,
        "public_key_sha256": None,
        "trust_status": None,
        "key_rotation_id": None,
    }


def _validate_artifact_manifest(
    document: Mapping[str, Any], *, with_digest: bool
) -> None:
    expected = {"schema_version", "kind", "content_free", "entries"}
    if with_digest:
        expected.add("artifacts_sha256")
    require_exact_fields(document, "artifact manifest", expected)
    _validate_envelope(document, ARTIFACT_MANIFEST_KIND, "artifact manifest")
    if with_digest:
        require_sha256(document.get("artifacts_sha256"), "artifacts_sha256")
    entries = require_list(document.get("entries"), "artifact entries", limit=256)
    ids: list[str] = []
    for index, item in enumerate(entries):
        entry = require_mapping(item, f"artifact entries[{index}]")
        require_exact_fields(
            entry,
            f"artifact entries[{index}]",
            {
                "artifact_id",
                "media_type",
                "byte_count",
                "sha256",
                "summary_sha256",
                "content_included",
            },
        )
        ids.append(
            require_identity(
                entry.get("artifact_id"), f"artifact entries[{index}].artifact_id"
            )
        )
        require_string(entry.get("media_type"), f"artifact entries[{index}].media_type")
        require_non_negative_int(
            entry.get("byte_count"), f"artifact entries[{index}].byte_count"
        )
        require_sha256(entry.get("sha256"), f"artifact entries[{index}].sha256")
        require_optional_sha256(
            entry.get("summary_sha256"), f"artifact entries[{index}].summary_sha256"
        )
        if entry.get("content_included") is not False:
            raise CapsuleSchemaError(
                "artifact bytes are forbidden in the default capsule"
            )
    if ids != sorted(set(ids)):
        raise CapsuleSchemaError(
            "artifact entries must be sorted and unique by artifact_id"
        )


def _validate_omission_manifest(
    document: Mapping[str, Any], *, with_digest: bool
) -> None:
    expected = {"schema_version", "kind", "content_free", "entries"}
    if with_digest:
        expected.add("omissions_sha256")
    require_exact_fields(document, "omission manifest", expected)
    _validate_envelope(document, OMISSION_MANIFEST_KIND, "omission manifest")
    if with_digest:
        require_sha256(document.get("omissions_sha256"), "omissions_sha256")
    entries = require_list(document.get("entries"), "omission entries", limit=128)
    identities: list[tuple[str, str]] = []
    for index, item in enumerate(entries):
        entry = require_mapping(item, f"omission entries[{index}]")
        require_exact_fields(
            entry, f"omission entries[{index}]", {"code", "scope", "reason_code"}
        )
        code = require_identity(entry.get("code"), f"omission entries[{index}].code")
        scope = require_identity(entry.get("scope"), f"omission entries[{index}].scope")
        require_identity(
            entry.get("reason_code"), f"omission entries[{index}].reason_code"
        )
        identities.append((scope, code))
    if identities != sorted(set(identities)):
        raise CapsuleSchemaError("omission entries must be sorted and unique")


def _validate_run_capsule(document: Mapping[str, Any], *, with_identity: bool) -> None:
    expected = {
        "schema_version",
        "kind",
        "content_free",
        "created_at",
        "input_lock_sha256",
        "output_receipt_sha256",
        "artifacts_sha256",
        "omissions_sha256",
        "signature",
    }
    if with_identity:
        expected |= {"capsule_id", "capsule_sha256"}
    require_exact_fields(document, "run capsule", expected)
    _validate_envelope(document, RUN_CAPSULE_KIND, "run capsule")
    require_string(document.get("created_at"), "created_at")
    for field in (
        "input_lock_sha256",
        "output_receipt_sha256",
        "artifacts_sha256",
        "omissions_sha256",
    ):
        require_sha256(document.get(field), field)
    _validate_signature(document.get("signature"))
    if with_identity:
        require_identity(document.get("capsule_id"), "capsule_id")
        require_sha256(document.get("capsule_sha256"), "capsule_sha256")


def _validate_signature(value: Any) -> None:
    signature = require_mapping(value, "signature")
    require_exact_fields(
        signature,
        "signature",
        {
            "status",
            "algorithm",
            "signer_id",
            "public_key_sha256",
            "trust_status",
            "key_rotation_id",
        },
    )
    try:
        status = SignatureStatus(str(signature.get("status")))
    except ValueError as exc:
        raise CapsuleSchemaError("signature.status is invalid") from exc
    optional = (
        "algorithm",
        "signer_id",
        "public_key_sha256",
        "trust_status",
        "key_rotation_id",
    )
    if status is SignatureStatus.UNSIGNED:
        if any(signature.get(field) is not None for field in optional):
            raise CapsuleSchemaError("unsigned capsule must not claim signer metadata")
        return
    if signature.get("algorithm") != "Ed25519":
        raise CapsuleSchemaError("signed capsule algorithm must be Ed25519")
    require_identity(signature.get("signer_id"), "signature.signer_id")
    require_sha256(signature.get("public_key_sha256"), "signature.public_key_sha256")
    require_identity(signature.get("trust_status"), "signature.trust_status")
    require_identity(signature.get("key_rotation_id"), "signature.key_rotation_id")


def _validate_envelope(document: Mapping[str, Any], kind: str, field: str) -> None:
    if document.get("schema_version") != RUN_CAPSULE_SCHEMA_VERSION:
        raise CapsuleSchemaError(f"unsupported {field} schema_version")
    if document.get("kind") != kind or document.get("content_free") is not True:
        raise CapsuleSchemaError(f"{field} identity or content policy is invalid")
    validate_content_free(document)


def _verify_digest(document: Mapping[str, Any], digest_field: str, field: str) -> None:
    expected = document[digest_field]
    body = {key: value for key, value in document.items() if key != digest_field}
    if canonical_sha256(body) != expected:
        raise CapsuleIntegrityError(f"{field} digest does not match")
