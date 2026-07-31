"""Immutable inputs for hermetic upgrade comparisons."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping, cast

from gigaloom.contracts import (
    CompatibilityObservationV1,
    RouteEvidenceV1,
    compatibility_observation_from_dict,
    compatibility_observation_to_dict,
)


UPGRADE_RADAR_SCHEMA_VERSION = 1
MAX_CORPUS_CASES = 32
MAX_CASE_REQUIREMENTS = 32

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class SealedCorpusCaseV1:
    """One content-free comparison case bound to external fixture digests."""

    case_id: str
    request_digest: str
    expectation_digest: str
    required_capabilities: tuple[str, ...]
    required_gates: tuple[str, ...]
    schema_version: int = UPGRADE_RADAR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, "sealed corpus case")
        _validate_identity(self.case_id, "sealed corpus case id")
        _validate_digest(self.request_digest, "sealed corpus request digest")
        _validate_digest(
            self.expectation_digest,
            "sealed corpus expectation digest",
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _normalize_identities(
                self.required_capabilities,
                "sealed corpus required capabilities",
            ),
        )
        object.__setattr__(
            self,
            "required_gates",
            _normalize_identities(
                self.required_gates,
                "sealed corpus required gates",
                allow_empty=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class SealedCorpusV1:
    """Bounded content-free corpus whose canonical body has one exact digest."""

    corpus_id: str
    cases: tuple[SealedCorpusCaseV1, ...]
    schema_version: int = UPGRADE_RADAR_SCHEMA_VERSION
    sealed_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, "sealed corpus")
        _validate_identity(self.corpus_id, "sealed corpus id")
        if (
            not isinstance(self.cases, tuple)
            or not self.cases
            or len(self.cases) > MAX_CORPUS_CASES
            or any(not isinstance(item, SealedCorpusCaseV1) for item in self.cases)
        ):
            raise ValueError("sealed corpus cases must be a bounded non-empty tuple")
        normalized = tuple(sorted(self.cases, key=lambda item: item.case_id))
        if len({item.case_id for item in normalized}) != len(normalized):
            raise ValueError("sealed corpus case ids must be unique")
        object.__setattr__(self, "cases", normalized)
        object.__setattr__(self, "sealed_digest", _digest(_corpus_body(self)))


@dataclass(frozen=True, slots=True)
class RouteSnapshotV1:
    """Exact route, executable, protocol, and capability evidence snapshot."""

    route: RouteEvidenceV1
    compatibility: CompatibilityObservationV1
    command_tokens_digest: str
    model_identity: str
    schema_version: int = UPGRADE_RADAR_SCHEMA_VERSION
    snapshot_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version, "route snapshot")
        if not isinstance(self.route, RouteEvidenceV1):
            raise ValueError("route snapshot route evidence is invalid")
        if not isinstance(self.compatibility, CompatibilityObservationV1):
            raise ValueError("route snapshot compatibility evidence is invalid")
        _validate_digest(
            self.command_tokens_digest,
            "route snapshot command tokens digest",
        )
        _validate_identity(self.model_identity, "route snapshot model identity")
        if (
            self.route.route_id != self.compatibility.route_id
            or self.route.capability_fingerprint
            != self.compatibility.capability_fingerprint
            or self.route.compatibility_observation_digest
            != self.compatibility.probe_digest
        ):
            raise ValueError("route snapshot evidence does not match compatibility")
        object.__setattr__(self, "snapshot_digest", _digest(_route_body(self)))


def sealed_corpus_to_dict(corpus: SealedCorpusV1) -> dict[str, Any]:
    """Serialize one sealed corpus with its verified canonical digest."""
    return {**_corpus_body(corpus), "sealed_digest": corpus.sealed_digest}


def sealed_corpus_from_dict(payload: Mapping[str, Any]) -> SealedCorpusV1:
    """Strictly decode a sealed corpus and reject digest drift."""
    value = _mapping(
        payload,
        {
            "schema_version",
            "corpus_id",
            "cases",
            "sealed_digest",
        },
        "sealed corpus",
    )
    raw_cases = value["cases"]
    if not isinstance(raw_cases, list):
        raise ValueError("sealed corpus cases must be an array")
    cases = tuple(_case_from_dict(item) for item in raw_cases)
    corpus = SealedCorpusV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        corpus_id=_string(value["corpus_id"], "corpus_id"),
        cases=cases,
    )
    declared = _string(value["sealed_digest"], "sealed_digest")
    if declared != corpus.sealed_digest:
        raise ValueError("sealed corpus digest does not match canonical body")
    return corpus


def route_snapshot_to_dict(snapshot: RouteSnapshotV1) -> dict[str, Any]:
    """Serialize exact route provenance without command or provider content."""
    return {**_route_body(snapshot), "snapshot_digest": snapshot.snapshot_digest}


def route_snapshot_from_dict(payload: Mapping[str, Any]) -> RouteSnapshotV1:
    """Strictly decode and verify one immutable route snapshot."""
    value = _mapping(
        payload,
        {
            "schema_version",
            "route",
            "compatibility",
            "command_tokens_digest",
            "model_identity",
            "snapshot_digest",
        },
        "route snapshot",
    )
    route_payload = _mapping(
        value["route"],
        {
            "schema_version",
            "route_id",
            "revision_digest",
            "capability_fingerprint",
            "compatibility_observation_digest",
        },
        "route evidence",
    )
    snapshot = RouteSnapshotV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        route=RouteEvidenceV1(
            schema_version=_integer(
                route_payload["schema_version"],
                "route.schema_version",
            ),
            route_id=_string(route_payload["route_id"], "route.route_id"),
            revision_digest=_string(
                route_payload["revision_digest"],
                "route.revision_digest",
            ),
            capability_fingerprint=_string(
                route_payload["capability_fingerprint"],
                "route.capability_fingerprint",
            ),
            compatibility_observation_digest=_string(
                route_payload["compatibility_observation_digest"],
                "route.compatibility_observation_digest",
            ),
        ),
        compatibility=compatibility_observation_from_dict(
            _mapping_object(value["compatibility"], "compatibility")
        ),
        command_tokens_digest=_string(
            value["command_tokens_digest"],
            "command_tokens_digest",
        ),
        model_identity=_string(value["model_identity"], "model_identity"),
    )
    declared = _string(value["snapshot_digest"], "snapshot_digest")
    if declared != snapshot.snapshot_digest:
        raise ValueError("route snapshot digest does not match canonical body")
    return snapshot


def _corpus_body(corpus: SealedCorpusV1) -> dict[str, Any]:
    return {
        "schema_version": corpus.schema_version,
        "corpus_id": corpus.corpus_id,
        "cases": [_case_to_dict(item) for item in corpus.cases],
    }


def _case_to_dict(case: SealedCorpusCaseV1) -> dict[str, Any]:
    return {
        "schema_version": case.schema_version,
        "case_id": case.case_id,
        "request_digest": case.request_digest,
        "expectation_digest": case.expectation_digest,
        "required_capabilities": list(case.required_capabilities),
        "required_gates": list(case.required_gates),
    }


def _case_from_dict(payload: object) -> SealedCorpusCaseV1:
    value = _mapping(
        payload,
        {
            "schema_version",
            "case_id",
            "request_digest",
            "expectation_digest",
            "required_capabilities",
            "required_gates",
        },
        "sealed corpus case",
    )
    return SealedCorpusCaseV1(
        schema_version=_integer(value["schema_version"], "case.schema_version"),
        case_id=_string(value["case_id"], "case.case_id"),
        request_digest=_string(value["request_digest"], "case.request_digest"),
        expectation_digest=_string(
            value["expectation_digest"],
            "case.expectation_digest",
        ),
        required_capabilities=_string_tuple(
            value["required_capabilities"],
            "case.required_capabilities",
        ),
        required_gates=_string_tuple(
            value["required_gates"],
            "case.required_gates",
        ),
    )


def _route_body(snapshot: RouteSnapshotV1) -> dict[str, Any]:
    route = snapshot.route
    return {
        "schema_version": snapshot.schema_version,
        "route": {
            "schema_version": route.schema_version,
            "route_id": route.route_id,
            "revision_digest": route.revision_digest,
            "capability_fingerprint": route.capability_fingerprint,
            "compatibility_observation_digest": (
                route.compatibility_observation_digest
            ),
        },
        "compatibility": compatibility_observation_to_dict(snapshot.compatibility),
        "command_tokens_digest": snapshot.command_tokens_digest,
        "model_identity": snapshot.model_identity,
    }


def _normalize_identities(
    values: object,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_CASE_REQUIREMENTS
        or (not allow_empty and not values)
    ):
        raise ValueError(f"{field_name} must be a bounded tuple")
    normalized = tuple(_validate_identity(item, field_name) for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(normalized))


def _validate_schema(value: object, field_name: str) -> None:
    if value != UPGRADE_RADAR_SCHEMA_VERSION:
        raise ValueError(f"unsupported {field_name} schema_version")


def _validate_identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return value


def _mapping(
    value: object,
    required: set[str],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    keys = set(value)
    if keys != required:
        raise ValueError(f"{field_name} has unknown or missing fields")
    return cast(Mapping[str, Any], value)


def _mapping_object(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return cast(Mapping[str, Any], value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string array")
    return cast(tuple[str, ...], tuple(value))


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "RouteSnapshotV1",
    "SealedCorpusCaseV1",
    "SealedCorpusV1",
    "route_snapshot_from_dict",
    "route_snapshot_to_dict",
    "sealed_corpus_from_dict",
    "sealed_corpus_to_dict",
]
