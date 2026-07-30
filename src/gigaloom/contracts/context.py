"""Source-bound, content-free context manifest contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping, TypeVar


CONTEXT_MANIFEST_SCHEMA_VERSION = 1
CONTEXT_MANIFEST_FORMAT = "gigaloom.context-manifest.v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_T = TypeVar("_T")


class ContextEntryKind(str, Enum):
    """Observable input kinds represented by a context manifest."""

    INSTRUCTION = "instruction"
    FILE = "file"
    SYMBOL = "symbol"
    PROJECT_MEMORY = "project_memory"
    TOOL = "tool"


class InclusionReason(str, Enum):
    """Reviewed reasons for including an observable input."""

    MANDATORY_INSTRUCTION = "mandatory_instruction"
    USER_SELECTION = "user_selection"
    PROJECT_RULE = "project_rule"
    DEPENDENCY = "dependency"
    TOOL_AVAILABLE = "tool_available"
    PRIOR_CONTEXT = "prior_context"


class OmissionReason(str, Enum):
    """Reviewed reasons for omitting an observable input."""

    TRUNCATED = "truncated"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    BUDGET = "budget"


class TokenEstimateMethod(str, Enum):
    """Method used to produce a token observation."""

    PROVIDER_REPORTED = "provider_reported"
    TOKENIZER = "tokenizer"
    HEURISTIC = "heuristic"
    UNAVAILABLE = "unavailable"


class TokenEstimateConfidence(str, Enum):
    """Knowledge level for a token observation."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ContextEntry:
    """One observable, content-free context inclusion."""

    entry_id: str
    kind: ContextEntryKind
    source_digest: str
    inclusion_reason: InclusionReason
    relative_path: str | None = None
    symbol: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.entry_id, "entry_id")
        _validate_sha256(self.source_digest, "source_digest")
        _validate_relative_path(self.relative_path)
        _validate_optional_text(self.symbol, "symbol")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation."""
        return {
            "entry_id": self.entry_id,
            "kind": self.kind.value,
            "source_digest": self.source_digest,
            "inclusion_reason": self.inclusion_reason.value,
            "relative_path": self.relative_path,
            "symbol": self.symbol,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ContextOmission:
    """One observable source that was intentionally not included."""

    source_id: str
    source_kind: ContextEntryKind
    reason: OmissionReason
    source_digest: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.source_id, "source_id")
        if self.source_digest is not None:
            _validate_sha256(self.source_digest, "source_digest")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation."""
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind.value,
            "reason": self.reason.value,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class ContextOverride:
    """A content-free digest binding for an effective override."""

    key: str
    value_digest: str
    reason: str

    def __post_init__(self) -> None:
        _validate_identifier(self.key, "key")
        _validate_sha256(self.value_digest, "value_digest")
        _validate_identifier(self.reason, "reason")

    def to_dict(self) -> dict[str, str]:
        """Return the stable wire representation."""
        return {
            "key": self.key,
            "value_digest": self.value_digest,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CompactionBoundary:
    """One observable native or structured compaction boundary."""

    boundary_id: str
    mode: str
    source_manifest_digest: str
    upstream_event_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.boundary_id, "boundary_id")
        if self.mode not in {"structured", "native_only"}:
            raise ValueError("mode must be structured or native_only")
        _validate_sha256(self.source_manifest_digest, "source_manifest_digest")
        _validate_optional_text(self.upstream_event_id, "upstream_event_id")

    def to_dict(self) -> dict[str, str | None]:
        """Return the stable wire representation."""
        return {
            "boundary_id": self.boundary_id,
            "mode": self.mode,
            "source_manifest_digest": self.source_manifest_digest,
            "upstream_event_id": self.upstream_event_id,
        }


@dataclass(frozen=True)
class TokenEstimate:
    """Content-free token count with explicit method and confidence."""

    scope_id: str
    token_count: int | None
    method: TokenEstimateMethod
    confidence: TokenEstimateConfidence

    def __post_init__(self) -> None:
        _validate_identifier(self.scope_id, "scope_id")
        if self.token_count is not None and self.token_count < 0:
            raise ValueError("token_count must be non-negative")
        if (
            self.confidence is TokenEstimateConfidence.UNKNOWN
            and self.token_count is not None
        ):
            raise ValueError("unknown token estimate cannot carry token_count")
        if (
            self.method is TokenEstimateMethod.UNAVAILABLE
            and self.confidence is not TokenEstimateConfidence.UNKNOWN
        ):
            raise ValueError("unavailable token method requires unknown confidence")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation."""
        return {
            "scope_id": self.scope_id,
            "token_count": self.token_count,
            "method": self.method.value,
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class ProviderManagedUnknown:
    """Provider-owned context that GigaLoom cannot observe."""

    provider: str
    scope: str
    reason: str

    def __post_init__(self) -> None:
        _validate_identifier(self.provider, "provider")
        _validate_identifier(self.scope, "scope")
        _validate_identifier(self.reason, "reason")

    def to_dict(self) -> dict[str, str]:
        """Return the stable wire representation."""
        return {
            "provider": self.provider,
            "scope": self.scope,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContextManifest:
    """Immutable manifest of context observable by GigaLoom."""

    manifest_id: str
    schema_version: int
    source_revision: str
    config_digest: str
    entries: tuple[ContextEntry, ...]
    omissions: tuple[ContextOmission, ...]
    overrides: tuple[ContextOverride, ...]
    compaction_boundaries: tuple[CompactionBoundary, ...]
    token_estimates: tuple[TokenEstimate, ...]
    provider_managed_unknowns: tuple[ProviderManagedUnknown, ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported ContextManifest schema_version")
        _validate_identifier(self.source_revision, "source_revision")
        _validate_sha256(self.config_digest, "config_digest")
        _validate_sha256(self.manifest_digest, "manifest_digest")
        expected_digest = _manifest_digest(self._digest_payload())
        if self.manifest_digest != expected_digest:
            raise ValueError("manifest_digest does not match manifest payload")
        if self.manifest_id != _manifest_id(expected_digest):
            raise ValueError("manifest_id does not match manifest_digest")

    def _digest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_revision": self.source_revision,
            "config_digest": self.config_digest,
            "entries": [item.to_dict() for item in self.entries],
            "omissions": [item.to_dict() for item in self.omissions],
            "overrides": [item.to_dict() for item in self.overrides],
            "compaction_boundaries": [
                item.to_dict() for item in self.compaction_boundaries
            ],
            "token_estimates": [item.to_dict() for item in self.token_estimates],
            "provider_managed_unknowns": [
                item.to_dict() for item in self.provider_managed_unknowns
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the stable, content-free wire representation."""
        return {
            "manifest_id": self.manifest_id,
            **self._digest_payload(),
            "manifest_digest": self.manifest_digest,
        }


def build_context_manifest(
    *,
    source_revision: str,
    config_digest: str,
    entries: Iterable[ContextEntry] = (),
    omissions: Iterable[ContextOmission] = (),
    overrides: Iterable[ContextOverride] = (),
    compaction_boundaries: Iterable[CompactionBoundary] = (),
    token_estimates: Iterable[TokenEstimate] = (),
    provider_managed_unknowns: Iterable[ProviderManagedUnknown] = (),
) -> ContextManifest:
    """Build a deterministically ordered and digested manifest."""
    normalized_entries = _sorted_tuple(entries)
    normalized_omissions = _sorted_tuple(omissions)
    normalized_overrides = _sorted_tuple(overrides)
    normalized_boundaries = _sorted_tuple(compaction_boundaries)
    normalized_estimates = _sorted_tuple(token_estimates)
    normalized_unknowns = _sorted_tuple(provider_managed_unknowns)
    digest = _manifest_digest(
        {
            "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
            "source_revision": source_revision,
            "config_digest": config_digest,
            "entries": [item.to_dict() for item in normalized_entries],
            "omissions": [item.to_dict() for item in normalized_omissions],
            "overrides": [item.to_dict() for item in normalized_overrides],
            "compaction_boundaries": [item.to_dict() for item in normalized_boundaries],
            "token_estimates": [item.to_dict() for item in normalized_estimates],
            "provider_managed_unknowns": [
                item.to_dict() for item in normalized_unknowns
            ],
        }
    )
    return ContextManifest(
        manifest_id=_manifest_id(digest),
        schema_version=CONTEXT_MANIFEST_SCHEMA_VERSION,
        source_revision=source_revision,
        config_digest=config_digest,
        entries=normalized_entries,
        omissions=normalized_omissions,
        overrides=normalized_overrides,
        compaction_boundaries=normalized_boundaries,
        token_estimates=normalized_estimates,
        provider_managed_unknowns=normalized_unknowns,
        manifest_digest=digest,
    )


def context_manifest_schema() -> dict[str, Any]:
    """Describe the stable v1 shape and its safety bindings."""
    return {
        "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
        "format": CONTEXT_MANIFEST_FORMAT,
        "digest": "sha256-canonical-json-v1",
        "cache_binding": ["source_revision", "config_digest"],
        "content_free": True,
        "entry_kinds": [item.value for item in ContextEntryKind],
        "inclusion_reasons": [item.value for item in InclusionReason],
        "omission_reasons": [item.value for item in OmissionReason],
        "token_estimate_methods": [item.value for item in TokenEstimateMethod],
        "token_estimate_confidence": [item.value for item in TokenEstimateConfidence],
    }


def _manifest_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_id(digest: str) -> str:
    return f"ctxm_{digest[:24]}"


def _sorted_tuple(items: Iterable[_T]) -> tuple[_T, ...]:
    return tuple(
        sorted(
            items,
            key=lambda item: json.dumps(
                item.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    )


def _validate_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{field_name} must be 1..256 characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{field_name} contains forbidden control characters")


def _validate_optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        _validate_identifier(value, field_name)


def _validate_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_relative_path(value: str | None) -> None:
    if value is None:
        return
    _validate_identifier(value, "relative_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("relative_path must stay within the source root")


__all__ = [
    "CONTEXT_MANIFEST_FORMAT",
    "CONTEXT_MANIFEST_SCHEMA_VERSION",
    "CompactionBoundary",
    "ContextEntry",
    "ContextEntryKind",
    "ContextManifest",
    "ContextOmission",
    "ContextOverride",
    "InclusionReason",
    "OmissionReason",
    "ProviderManagedUnknown",
    "TokenEstimate",
    "TokenEstimateConfidence",
    "TokenEstimateMethod",
    "build_context_manifest",
    "context_manifest_schema",
]
