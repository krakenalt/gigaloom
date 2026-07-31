"""Explicit Ed25519 signing and signature-manifest verification."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import (
    canonical_json_bytes,
    canonical_sha256,
    parse_canonical_json_bytes,
    require_exact_fields,
    require_identity,
    require_list,
    require_mapping,
    require_non_negative_int,
    require_sha256,
    require_string,
)
from .errors import CapsuleIntegrityError, CapsuleSchemaError
from .models import RUN_CAPSULE_SCHEMA_VERSION, RunCapsule, SignatureStatus

SIGNATURE_MANIFEST_KIND = "gigaloom.run_capsule.signature_manifest.v1"
PUBLIC_KEY_KIND = "gigaloom.run_capsule.public_key.v1"
SIGNED_MEMBER_PATHS = (
    "artifacts.json",
    "capsule.json",
    "input-lock.json",
    "omissions.json",
    "output-receipt.json",
)


class CapsuleSigner(Protocol):
    """Explicit signer port; callers own private-key configuration and lifetime."""

    signer_id: str
    trust_status: str
    key_rotation_id: str

    def public_key_bytes(self) -> bytes:
        """Return the raw 32-byte Ed25519 public key."""

    def sign(self, message: bytes) -> bytes:
        """Sign exact canonical manifest bytes."""


@dataclass(frozen=True, slots=True)
class Ed25519Signer:
    """Ed25519 signer created only from an explicit private-key reference."""

    signer_id: str
    trust_status: str
    key_rotation_id: str
    _private_key: Ed25519PrivateKey = field(repr=False, compare=False)

    @classmethod
    def from_private_bytes(
        cls,
        private_key: bytes,
        *,
        signer_id: str,
        trust_status: str,
        key_rotation_id: str,
    ) -> Ed25519Signer:
        """Load an explicit raw 32-byte key; no key is generated or persisted."""
        if len(private_key) != 32:
            raise CapsuleSchemaError("Ed25519 private key must contain 32 raw bytes")
        for value, name in (
            (signer_id, "signer_id"),
            (trust_status, "trust_status"),
            (key_rotation_id, "key_rotation_id"),
        ):
            require_identity(value, name)
        return cls(
            signer_id=signer_id,
            trust_status=trust_status,
            key_rotation_id=key_rotation_id,
            _private_key=Ed25519PrivateKey.from_private_bytes(private_key),
        )

    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


def signed_metadata(signer: CapsuleSigner) -> dict[str, str]:
    """Return signer claims bound into the root capsule manifest."""
    public_key = signer.public_key_bytes()
    if len(public_key) != 32:
        raise CapsuleSchemaError("Ed25519 public key must contain 32 raw bytes")
    return {
        "status": SignatureStatus.SIGNED.value,
        "algorithm": "Ed25519",
        "signer_id": require_identity(signer.signer_id, "signer_id"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "trust_status": require_identity(signer.trust_status, "trust_status"),
        "key_rotation_id": require_identity(signer.key_rotation_id, "key_rotation_id"),
    }


def build_signature_material(
    *,
    capsule: RunCapsule,
    content_files: Mapping[str, bytes],
    created_at: str,
    signer: CapsuleSigner | None,
) -> tuple[bytes, bytes | None, bytes | None]:
    """Build the canonical signature manifest and optional Ed25519 material."""
    signature = capsule.to_dict()["signature"]
    entries = []
    for path in SIGNED_MEMBER_PATHS:
        data = content_files[path]
        entries.append(
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_count": len(data),
            }
        )
    body: dict[str, Any] = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": SIGNATURE_MANIFEST_KIND,
        "capsule_id": capsule.capsule_id,
        "capsule_sha256": capsule.sha256,
        "created_at": created_at,
        "signature": signature,
        "files": entries,
    }
    body["manifest_sha256"] = canonical_sha256(body)
    manifest = canonical_json_bytes(body)
    if signer is None:
        return manifest, None, None
    public_key = signer.public_key_bytes()
    public_document = canonical_json_bytes(
        {
            "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
            "kind": PUBLIC_KEY_KIND,
            "algorithm": "Ed25519",
            "signer_id": signer.signer_id,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
            "trust_status": signer.trust_status,
            "key_rotation_id": signer.key_rotation_id,
        }
    )
    return manifest, signer.sign(manifest), public_document


def verify_signature_material(
    *,
    manifest_bytes: bytes,
    content_files: Mapping[str, bytes],
    capsule: RunCapsule,
    signature_bytes: bytes | None,
    public_key_bytes: bytes | None,
) -> tuple[SignatureStatus, bool | None, str | None, str | None]:
    """Verify file digests and optional Ed25519 signature without trust escalation."""
    manifest = parse_canonical_json_bytes(manifest_bytes, "signature manifest")
    _validate_signature_manifest(manifest, capsule)
    expected_manifest_sha = manifest["manifest_sha256"]
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if canonical_sha256(body) != expected_manifest_sha:
        raise CapsuleIntegrityError("signature manifest digest does not match")
    for entry in manifest["files"]:
        path = entry["path"]
        data = content_files.get(path)
        if data is None:
            raise CapsuleIntegrityError(f"signed capsule member is missing: {path}")
        if (
            len(data) != entry["byte_count"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
        ):
            raise CapsuleIntegrityError(
                f"signed capsule member digest does not match: {path}"
            )
    metadata = manifest["signature"]
    status = SignatureStatus(metadata["status"])
    if status is SignatureStatus.UNSIGNED:
        if signature_bytes is not None or public_key_bytes is not None:
            raise CapsuleIntegrityError("unsigned capsule contains signature material")
        return status, None, None, None
    if signature_bytes is None or public_key_bytes is None:
        raise CapsuleIntegrityError("signed capsule is missing signature material")
    public_document = parse_canonical_json_bytes(public_key_bytes, "public key")
    raw_key = _validate_public_key(public_document, metadata)
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature_bytes, manifest_bytes
        )
    except (InvalidSignature, ValueError) as exc:
        raise CapsuleIntegrityError("Ed25519 signature is invalid") from exc
    return status, True, metadata["signer_id"], metadata["trust_status"]


def _validate_signature_manifest(
    manifest: Mapping[str, Any], capsule: RunCapsule
) -> None:
    require_exact_fields(
        manifest,
        "signature manifest",
        {
            "schema_version",
            "kind",
            "capsule_id",
            "capsule_sha256",
            "created_at",
            "signature",
            "files",
            "manifest_sha256",
        },
    )
    if (
        manifest.get("schema_version") != RUN_CAPSULE_SCHEMA_VERSION
        or manifest.get("kind") != SIGNATURE_MANIFEST_KIND
    ):
        raise CapsuleSchemaError("signature manifest identity is invalid")
    if (
        manifest.get("capsule_id") != capsule.capsule_id
        or manifest.get("capsule_sha256") != capsule.sha256
    ):
        raise CapsuleIntegrityError("signature manifest capsule binding does not match")
    require_string(manifest.get("created_at"), "signature manifest created_at")
    require_sha256(manifest.get("manifest_sha256"), "manifest_sha256")
    if manifest.get("signature") != capsule.to_dict()["signature"]:
        raise CapsuleIntegrityError("signature metadata does not match capsule")
    files = require_list(manifest.get("files"), "signature manifest files", limit=8)
    paths: list[str] = []
    for index, item in enumerate(files):
        entry = require_mapping(item, f"signature manifest files[{index}]")
        require_exact_fields(
            entry,
            f"signature manifest files[{index}]",
            {"path", "sha256", "byte_count"},
        )
        path = require_string(
            entry.get("path"), f"signature manifest files[{index}].path"
        )
        paths.append(path)
        require_sha256(entry.get("sha256"), f"signature manifest files[{index}].sha256")
        require_non_negative_int(
            entry.get("byte_count"), f"signature manifest files[{index}].byte_count"
        )
    if tuple(paths) != SIGNED_MEMBER_PATHS:
        raise CapsuleSchemaError("signature manifest file set is invalid")


def _validate_public_key(
    document: Mapping[str, Any], metadata: Mapping[str, Any]
) -> bytes:
    require_exact_fields(
        document,
        "public key",
        {
            "schema_version",
            "kind",
            "algorithm",
            "signer_id",
            "public_key_base64",
            "public_key_sha256",
            "trust_status",
            "key_rotation_id",
        },
    )
    if (
        document.get("schema_version") != RUN_CAPSULE_SCHEMA_VERSION
        or document.get("kind") != PUBLIC_KEY_KIND
        or document.get("algorithm") != "Ed25519"
    ):
        raise CapsuleSchemaError("public key identity is invalid")
    for metadata_field in (
        "signer_id",
        "public_key_sha256",
        "trust_status",
        "key_rotation_id",
    ):
        if document.get(metadata_field) != metadata.get(metadata_field):
            raise CapsuleIntegrityError(
                f"public key {metadata_field} does not match capsule"
            )
    encoded = require_string(document.get("public_key_base64"), "public_key_base64")
    try:
        raw_key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise CapsuleSchemaError("public key encoding is invalid") from exc
    if (
        len(raw_key) != 32
        or hashlib.sha256(raw_key).hexdigest() != document["public_key_sha256"]
    ):
        raise CapsuleIntegrityError("public key digest does not match")
    return raw_key
