"""Strict parsing for ContextManifest wire payloads."""

from __future__ import annotations

from typing import Any, Mapping, TypeVar

from gigaloom.contracts.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    CompactionBoundary,
    ContextEntry,
    ContextEntryKind,
    ContextManifest,
    ContextOmission,
    ContextOverride,
    InclusionReason,
    OmissionReason,
    ProviderManagedUnknown,
    TokenEstimate,
    TokenEstimateConfidence,
    TokenEstimateMethod,
    _sorted_tuple,
    _validate_identifier,
)


_T = TypeVar("_T")


def context_manifest_from_dict(data: Mapping[str, Any]) -> ContextManifest:
    """Parse and verify a strict ContextManifest v1 wire payload."""
    expected = {
        "manifest_id",
        "schema_version",
        "source_revision",
        "config_digest",
        "entries",
        "omissions",
        "overrides",
        "compaction_boundaries",
        "token_estimates",
        "provider_managed_unknowns",
        "manifest_digest",
    }
    _require_exact_fields(data, expected, "ContextManifest")
    if data["schema_version"] != CONTEXT_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported ContextManifest schema_version")
    return ContextManifest(
        manifest_id=_required_text(data["manifest_id"], "manifest_id"),
        schema_version=CONTEXT_MANIFEST_SCHEMA_VERSION,
        source_revision=_required_text(data["source_revision"], "source_revision"),
        config_digest=_required_text(data["config_digest"], "config_digest"),
        entries=_parse_items(data["entries"], _entry_from_dict, "entries"),
        omissions=_parse_items(data["omissions"], _omission_from_dict, "omissions"),
        overrides=_parse_items(data["overrides"], _override_from_dict, "overrides"),
        compaction_boundaries=_parse_items(
            data["compaction_boundaries"],
            _compaction_from_dict,
            "compaction_boundaries",
        ),
        token_estimates=_parse_items(
            data["token_estimates"], _token_estimate_from_dict, "token_estimates"
        ),
        provider_managed_unknowns=_parse_items(
            data["provider_managed_unknowns"],
            _provider_unknown_from_dict,
            "provider_managed_unknowns",
        ),
        manifest_digest=_required_text(data["manifest_digest"], "manifest_digest"),
    )


def _entry_from_dict(data: Mapping[str, Any]) -> ContextEntry:
    _require_exact_fields(
        data,
        {
            "entry_id",
            "kind",
            "source_digest",
            "inclusion_reason",
            "relative_path",
            "symbol",
            "size_bytes",
        },
        "ContextEntry",
    )
    size_bytes = data["size_bytes"]
    if size_bytes is not None and (
        not isinstance(size_bytes, int) or isinstance(size_bytes, bool)
    ):
        raise ValueError("size_bytes must be an integer or null")
    return ContextEntry(
        entry_id=_required_text(data["entry_id"], "entry_id"),
        kind=_enum_value(ContextEntryKind, data["kind"], "kind"),
        source_digest=_required_text(data["source_digest"], "source_digest"),
        inclusion_reason=_enum_value(
            InclusionReason, data["inclusion_reason"], "inclusion_reason"
        ),
        relative_path=_optional_string(data["relative_path"], "relative_path"),
        symbol=_optional_string(data["symbol"], "symbol"),
        size_bytes=size_bytes,
    )


def _omission_from_dict(data: Mapping[str, Any]) -> ContextOmission:
    _require_exact_fields(
        data,
        {"source_id", "source_kind", "reason", "source_digest"},
        "ContextOmission",
    )
    return ContextOmission(
        source_id=_required_text(data["source_id"], "source_id"),
        source_kind=_enum_value(ContextEntryKind, data["source_kind"], "source_kind"),
        reason=_enum_value(OmissionReason, data["reason"], "reason"),
        source_digest=_optional_string(data["source_digest"], "source_digest"),
    )


def _override_from_dict(data: Mapping[str, Any]) -> ContextOverride:
    _require_exact_fields(data, {"key", "value_digest", "reason"}, "ContextOverride")
    return ContextOverride(
        key=_required_text(data["key"], "key"),
        value_digest=_required_text(data["value_digest"], "value_digest"),
        reason=_required_text(data["reason"], "reason"),
    )


def _compaction_from_dict(data: Mapping[str, Any]) -> CompactionBoundary:
    _require_exact_fields(
        data,
        {"boundary_id", "mode", "source_manifest_digest", "upstream_event_id"},
        "CompactionBoundary",
    )
    return CompactionBoundary(
        boundary_id=_required_text(data["boundary_id"], "boundary_id"),
        mode=_required_text(data["mode"], "mode"),
        source_manifest_digest=_required_text(
            data["source_manifest_digest"], "source_manifest_digest"
        ),
        upstream_event_id=_optional_string(
            data["upstream_event_id"], "upstream_event_id"
        ),
    )


def _token_estimate_from_dict(data: Mapping[str, Any]) -> TokenEstimate:
    _require_exact_fields(
        data,
        {"scope_id", "token_count", "method", "confidence"},
        "TokenEstimate",
    )
    token_count = data["token_count"]
    if token_count is not None and (
        not isinstance(token_count, int) or isinstance(token_count, bool)
    ):
        raise ValueError("token_count must be an integer or null")
    return TokenEstimate(
        scope_id=_required_text(data["scope_id"], "scope_id"),
        token_count=token_count,
        method=_enum_value(TokenEstimateMethod, data["method"], "method"),
        confidence=_enum_value(
            TokenEstimateConfidence, data["confidence"], "confidence"
        ),
    )


def _provider_unknown_from_dict(data: Mapping[str, Any]) -> ProviderManagedUnknown:
    _require_exact_fields(
        data,
        {"provider", "scope", "reason"},
        "ProviderManagedUnknown",
    )
    return ProviderManagedUnknown(
        provider=_required_text(data["provider"], "provider"),
        scope=_required_text(data["scope"], "scope"),
        reason=_required_text(data["reason"], "reason"),
    )


def _parse_items(
    value: Any,
    parser: Any,
    field_name: str,
) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    parsed = tuple(parser(_required_mapping(item, field_name)) for item in value)
    return _sorted_tuple(parsed)


def _require_exact_fields(
    data: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be an object")
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise ValueError(f"{label} missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {sorted(unknown)}")


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} entries must be objects")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    _validate_identifier(value, field_name)
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _enum_value(enum_type: type[_T], value: Any, field_name: str) -> _T:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"unsupported {field_name}") from exc


__all__ = ["context_manifest_from_dict"]
