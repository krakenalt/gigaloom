"""Versioned, content-free contracts for protected source-to-sink flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping


SOURCE_TO_SINK_SCHEMA_VERSION = 1
MAX_INFLUENCE_SOURCES = 32
MAX_PREVIEW_CHARS = 256
MAX_DESTINATION_METADATA_CHARS = 2_048

_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ProvenanceClass(str, Enum):
    """Origin class for content that influenced a protected side effect."""

    USER = "user"
    REPO = "repo"
    WEB = "web"
    MCP = "mcp"
    ATTACHMENT = "attachment"
    TERMINAL = "terminal"
    GENERATED = "generated"


class TrustClass(str, Enum):
    """Reviewed trust level assigned at content ingress."""

    TRUSTED = "trusted"
    BOUNDED = "bounded"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"


class Sensitivity(str, Enum):
    """Highest known sensitivity of referenced content."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SECRET = "secret"


class SinkKind(str, Enum):
    """Protected sink families in the bounded 0.6 contract."""

    NETWORK_URL = "network_url"
    EXTERNAL_WRITE = "external_write"


class SinkDecisionStatus(str, Enum):
    """Deterministic source-to-sink admission result."""

    ALLOW = "allow"
    DENY = "deny"


class SinkDecisionReason(str, Enum):
    """Stable reason codes emitted by the deterministic guard."""

    ADMITTED = "admitted"
    SECRET_PAYLOAD = "secret_payload"
    SECRET_METADATA = "secret_metadata"
    SECRET_INFLUENCE = "secret_influence"
    UNKNOWN_PROVENANCE = "unknown_provenance"
    UNKNOWN_TRUST = "unknown_trust"
    UNTRUSTED_INFLUENCE = "untrusted_influence"
    NON_USER_AUTHORED = "non_user_authored"
    REDIRECT_REVALIDATION_REQUIRED = "redirect_revalidation_required"
    DESTINATION_BINDING_MISMATCH = "destination_binding_mismatch"
    PAYLOAD_BINDING_MISMATCH = "payload_binding_mismatch"


@dataclass(frozen=True, order=True)
class SourceRef:
    """Content-free identity and classification for one influencing source."""

    source_id: str
    provenance: ProvenanceClass | None
    trust: TrustClass
    sensitivity: Sensitivity
    content_sha256: str
    schema_version: int = SOURCE_TO_SINK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, field_name="source")
        _validate_identity(self.source_id, field_name="source_id")
        if self.provenance is not None and not isinstance(
            self.provenance,
            ProvenanceClass,
        ):
            raise ValueError("source provenance is invalid")
        if not isinstance(self.trust, TrustClass):
            raise ValueError("source trust is invalid")
        if not isinstance(self.sensitivity, Sensitivity):
            raise ValueError("source sensitivity is invalid")
        _validate_hash(self.content_sha256, field_name="source content_sha256")
        if self.provenance is None and self.trust is not TrustClass.UNKNOWN:
            raise ValueError("unknown provenance requires unknown trust")
        if (
            self.provenance in {ProvenanceClass.TERMINAL, ProvenanceClass.GENERATED}
            and self.trust is not TrustClass.UNTRUSTED
        ):
            raise ValueError("terminal and generated sources must be untrusted")


@dataclass(frozen=True)
class InfluenceSet:
    """Bounded, deterministically ordered sources influencing one request."""

    sources: tuple[SourceRef, ...]

    def __post_init__(self) -> None:
        normalized = tuple(sorted(self.sources, key=lambda item: item.source_id))
        if not normalized:
            raise ValueError("influence set must not be empty")
        if len(normalized) > MAX_INFLUENCE_SOURCES:
            raise ValueError("influence set exceeds source limit")
        if any(not isinstance(source, SourceRef) for source in normalized):
            raise ValueError("influence set contains an invalid source")
        source_ids = tuple(source.source_id for source in normalized)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("influence set contains duplicate source_id values")
        object.__setattr__(self, "sources", normalized)


@dataclass(frozen=True)
class SinkRequest:
    """Bounded request evaluated before authority approval can be consumed."""

    request_id: str
    sink_kind: SinkKind
    destination_metadata: str
    destination_sha256: str
    approved_destination_sha256: str
    payload_sha256: str
    approved_payload_sha256: str
    payload_preview: str
    payload_sensitivity: Sensitivity
    metadata_sensitivity: Sensitivity
    influence: InfluenceSet
    destination_source_ids: tuple[str, ...]
    payload_source_ids: tuple[str, ...]
    redirect_destination_sha256: str | None = None
    redirect_requires_revalidation: bool = False
    schema_version: int = SOURCE_TO_SINK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, field_name="sink request")
        _validate_identity(self.request_id, field_name="request_id")
        if not isinstance(self.sink_kind, SinkKind):
            raise ValueError("sink kind is invalid")
        _validate_bounded_text(
            self.destination_metadata,
            field_name="destination_metadata",
            max_chars=MAX_DESTINATION_METADATA_CHARS,
        )
        _validate_hash(self.destination_sha256, field_name="destination_sha256")
        _validate_hash(
            self.approved_destination_sha256,
            field_name="approved_destination_sha256",
        )
        _validate_hash(self.payload_sha256, field_name="payload_sha256")
        _validate_hash(
            self.approved_payload_sha256,
            field_name="approved_payload_sha256",
        )
        if not isinstance(self.payload_preview, str):
            raise ValueError("payload_preview must be text")
        if len(self.payload_preview) > MAX_PREVIEW_CHARS:
            raise ValueError("payload_preview exceeds character limit")
        if _CONTROL_RE.search(self.payload_preview):
            raise ValueError("payload_preview contains control characters")
        if not isinstance(self.payload_sensitivity, Sensitivity):
            raise ValueError("payload sensitivity is invalid")
        if not isinstance(self.metadata_sensitivity, Sensitivity):
            raise ValueError("metadata sensitivity is invalid")
        if (
            self.payload_sensitivity is Sensitivity.SECRET
            or self.metadata_sensitivity is Sensitivity.SECRET
        ) and self.payload_preview:
            raise ValueError("secret-bearing requests cannot retain a preview")
        if not isinstance(self.influence, InfluenceSet):
            raise ValueError("sink request influence is invalid")
        source_ids = {source.source_id for source in self.influence.sources}
        object.__setattr__(
            self,
            "destination_source_ids",
            _normalize_source_ids(
                self.destination_source_ids,
                source_ids=source_ids,
                field_name="destination_source_ids",
            ),
        )
        object.__setattr__(
            self,
            "payload_source_ids",
            _normalize_source_ids(
                self.payload_source_ids,
                source_ids=source_ids,
                field_name="payload_source_ids",
            ),
        )
        bound_source_ids = set(self.destination_source_ids) | set(
            self.payload_source_ids
        )
        if bound_source_ids != source_ids:
            raise ValueError("every influence source must be bound to the request")
        if self.redirect_destination_sha256 is not None:
            _validate_hash(
                self.redirect_destination_sha256,
                field_name="redirect_destination_sha256",
            )
        if not isinstance(self.redirect_requires_revalidation, bool):
            raise ValueError("redirect_requires_revalidation must be boolean")


@dataclass(frozen=True)
class SinkDecision:
    """Stable admission outcome bound to the exact request digest."""

    status: SinkDecisionStatus
    reason: SinkDecisionReason
    request_sha256: str
    requires_authority_approval: bool
    schema_version: int = SOURCE_TO_SINK_SCHEMA_VERSION
    decision_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, field_name="sink decision")
        if not isinstance(self.status, SinkDecisionStatus):
            raise ValueError("sink decision status is invalid")
        if not isinstance(self.reason, SinkDecisionReason):
            raise ValueError("sink decision reason is invalid")
        _validate_hash(self.request_sha256, field_name="request_sha256")
        if not isinstance(self.requires_authority_approval, bool):
            raise ValueError("requires_authority_approval must be boolean")
        if self.status is SinkDecisionStatus.ALLOW:
            if self.reason is not SinkDecisionReason.ADMITTED:
                raise ValueError("allowed decision requires admitted reason")
            if not self.requires_authority_approval:
                raise ValueError("allowed decision must require authority approval")
        elif self.requires_authority_approval:
            raise ValueError("denied decision cannot request authority approval")
        object.__setattr__(
            self,
            "decision_sha256",
            _canonical_hash(_sink_decision_payload(self)),
        )


@dataclass(frozen=True)
class DataFlowReceipt:
    """Immutable, non-secret projection of one source-to-sink decision."""

    receipt_id: str
    request_sha256: str
    sink_kind: SinkKind
    destination_metadata: str
    destination_sha256: str
    payload_sha256: str
    payload_preview: str
    sources: tuple[SourceRef, ...]
    decision: SinkDecision
    schema_version: int = SOURCE_TO_SINK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, field_name="data-flow receipt")
        _validate_hash(self.receipt_id, field_name="receipt_id")
        _validate_hash(self.request_sha256, field_name="request_sha256")
        if not isinstance(self.sink_kind, SinkKind):
            raise ValueError("receipt sink kind is invalid")
        _validate_bounded_text(
            self.destination_metadata,
            field_name="destination_metadata",
            max_chars=MAX_DESTINATION_METADATA_CHARS,
        )
        _validate_hash(self.destination_sha256, field_name="destination_sha256")
        _validate_hash(self.payload_sha256, field_name="payload_sha256")
        if not isinstance(self.payload_preview, str):
            raise ValueError("receipt payload_preview must be text")
        if len(self.payload_preview) > MAX_PREVIEW_CHARS:
            raise ValueError("receipt payload_preview exceeds character limit")
        normalized_sources = tuple(
            sorted(self.sources, key=lambda item: item.source_id)
        )
        if not normalized_sources or len(normalized_sources) > MAX_INFLUENCE_SOURCES:
            raise ValueError("receipt sources are invalid")
        if len({source.source_id for source in normalized_sources}) != len(
            normalized_sources
        ):
            raise ValueError("receipt contains duplicate source_id values")
        if not isinstance(self.decision, SinkDecision):
            raise ValueError("receipt decision is invalid")
        if self.decision.request_sha256 != self.request_sha256:
            raise ValueError("receipt decision request digest does not match")
        object.__setattr__(self, "sources", normalized_sources)


def _sink_decision_payload(decision: SinkDecision) -> dict[str, Any]:
    return {
        "schema_version": decision.schema_version,
        "status": decision.status.value,
        "reason": decision.reason.value,
        "request_sha256": decision.request_sha256,
        "requires_authority_approval": decision.requires_authority_approval,
    }


def _normalize_source_ids(
    values: tuple[str, ...],
    *,
    source_ids: set[str],
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{field_name} must be a non-empty tuple")
    normalized = tuple(sorted(values))
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} contains duplicates")
    for value in normalized:
        _validate_identity(value, field_name=field_name)
        if value not in source_ids:
            raise ValueError(f"{field_name} references an unknown source")
    return normalized


def _validate_schema(value: Any, *, field_name: str) -> None:
    if value != SOURCE_TO_SINK_SCHEMA_VERSION:
        raise ValueError(f"unsupported {field_name} schema_version")


def _validate_identity(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _validate_hash(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase sha256 hash")


def _validate_bounded_text(
    value: Any,
    *,
    field_name: str,
    max_chars: int,
) -> None:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(f"{field_name} is invalid")
    if _CONTROL_RE.search(value):
        raise ValueError(f"{field_name} contains control characters")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DataFlowReceipt",
    "InfluenceSet",
    "MAX_DESTINATION_METADATA_CHARS",
    "MAX_INFLUENCE_SOURCES",
    "MAX_PREVIEW_CHARS",
    "ProvenanceClass",
    "SOURCE_TO_SINK_SCHEMA_VERSION",
    "Sensitivity",
    "SinkDecision",
    "SinkDecisionReason",
    "SinkDecisionStatus",
    "SinkKind",
    "SinkRequest",
    "SourceRef",
    "TrustClass",
]
