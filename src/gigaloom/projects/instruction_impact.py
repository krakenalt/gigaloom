"""Read-only Effective Instructions impact projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from gigaloom.contracts.context import (
    ContextEntryKind,
    ContextFreshness,
    InclusionReason,
    OmissionReason,
    TokenEstimateConfidence,
    TokenEstimateMethod,
)
from gigaloom.contracts.context_lens import (
    ContextDisposition,
    ContextLensProjection,
    ContextSourceDescriptor,
    compile_context_lens,
)

from .instruction_discovery import (
    DiscoveredProjectInstructionV1,
    ProjectInstructionDiscoveryV1,
    ProjectInstructionKind,
    ProjectInstructionScope,
)


EFFECTIVE_INSTRUCTIONS_FORMAT = "gigaloom.effective-instructions.v1"


class InstructionUncertaintyKind(str, Enum):
    """Why an Effective Instructions claim is incomplete."""

    ADAPTER_REVISION_UNKNOWN = "adapter_revision_unknown"
    DISCOVERY_TRUNCATED = "discovery_truncated"
    OWNER_PRECEDENCE_UNKNOWN = "owner_precedence_unknown"
    SOURCE_OMITTED = "source_omitted"
    STALE_ADAPTER_REVISION = "stale_adapter_revision"


class InstructionConflictKind(str, Enum):
    """Content-free overlap requiring owner-specific review."""

    CROSS_OWNER_SCOPE_OVERLAP = "cross_owner_scope_overlap"
    OWNER_PRECEDENCE_AMBIGUOUS = "owner_precedence_ambiguous"


@dataclass(frozen=True)
class EffectiveInstructionSourceV1:
    """One source plus its effective scope, precedence, and disposition."""

    source_id: str
    selector_id: str
    kind: ProjectInstructionKind
    relative_path: str
    scope_path: str
    source_digest: str
    size_bytes: int
    materialization_owner: str
    materialization_revision: str | None
    freshness: ContextFreshness
    disposition: ContextDisposition
    reason: InclusionReason | OmissionReason
    precedence: int | None
    token_estimate: int

    def to_dict(self) -> dict[str, Any]:
        """Return the stable content-free representation."""
        return {
            "source_id": self.source_id,
            "selector_id": self.selector_id,
            "kind": self.kind.value,
            "relative_path": self.relative_path,
            "scope_path": self.scope_path,
            "source_digest": self.source_digest,
            "size_bytes": self.size_bytes,
            "materialization_owner": self.materialization_owner,
            "materialization_revision": self.materialization_revision,
            "freshness": self.freshness.value,
            "disposition": self.disposition.value,
            "reason": self.reason.value,
            "precedence": self.precedence,
            "token_estimate": self.token_estimate,
        }


@dataclass(frozen=True)
class EffectiveInstructionConflictV1:
    """Potential overlap without reading or merging source content."""

    kind: InstructionConflictKind
    source_ids: tuple[str, str]
    materialization_owners: tuple[str, ...]
    resolution: str

    def to_dict(self) -> dict[str, Any]:
        """Return the stable review fact."""
        return {
            "kind": self.kind.value,
            "source_ids": list(self.source_ids),
            "materialization_owners": list(self.materialization_owners),
            "resolution": self.resolution,
        }


@dataclass(frozen=True)
class EffectiveInstructionUncertaintyV1:
    """Explicit uncertainty bound to one owner or source when available."""

    kind: InstructionUncertaintyKind
    reason: str
    source_id: str | None = None
    materialization_owner: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Return the stable public representation."""
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "source_id": self.source_id,
            "materialization_owner": self.materialization_owner,
        }


@dataclass(frozen=True)
class EffectiveInstructionsProjectionV1:
    """Content-free projection; never a materialized combined prompt."""

    source_revision: str
    discovery_digest: str
    config_digest: str
    target_path: str
    sources: tuple[EffectiveInstructionSourceV1, ...]
    conflicts: tuple[EffectiveInstructionConflictV1, ...]
    uncertainties: tuple[EffectiveInstructionUncertaintyV1, ...]
    lens: ContextLensProjection
    launch_ready: bool
    format: str = EFFECTIVE_INSTRUCTIONS_FORMAT
    read_only: bool = True
    auto_materialized: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return summary evidence without source or prompt text."""
        return {
            "format": self.format,
            "source_revision": self.source_revision,
            "discovery_digest": self.discovery_digest,
            "config_digest": self.config_digest,
            "target_path": self.target_path,
            "sources": [item.to_dict() for item in self.sources],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "uncertainties": [item.to_dict() for item in self.uncertainties],
            "lens": self.lens.to_dict(),
            "launch_ready": self.launch_ready,
            "read_only": self.read_only,
            "auto_materialized": self.auto_materialized,
        }


def compile_effective_instructions(
    discovery: ProjectInstructionDiscoveryV1,
    *,
    target_path: str = "",
    selected_materialization_owners: Iterable[str] | None = None,
    selected_source_ids: Iterable[str] = (),
    materialization_revisions: Mapping[str, str] | None = None,
    expected_materialization_revisions: Mapping[str, str] | None = None,
) -> EffectiveInstructionsProjectionV1:
    """Compile exact discovery facts into one existing Context Lens owner."""
    target = _normalize_target_path(target_path)
    selected_owners = _optional_identifier_set(selected_materialization_owners)
    selected_sources = _identifier_set(selected_source_ids, "selected_source_ids")
    revisions = _revision_map(materialization_revisions)
    expected_revisions = _revision_map(expected_materialization_revisions)
    config_digest = _config_digest(
        discovery=discovery,
        target_path=target,
        selected_owners=selected_owners,
        selected_sources=selected_sources,
        revisions=revisions,
        expected_revisions=expected_revisions,
    )
    sources = tuple(
        _project_source(
            source,
            target_path=target,
            selected_owners=selected_owners,
            selected_sources=selected_sources,
            revisions=revisions,
            expected_revisions=expected_revisions,
        )
        for source in discovery.sources
    )
    uncertainties = _uncertainties(
        discovery,
        sources,
        revisions=revisions,
        expected_revisions=expected_revisions,
    )
    lens = compile_context_lens(
        source_revision=discovery.source_revision,
        config_digest=config_digest,
        sources=tuple(_lens_source(item) for item in sources),
    )
    conflicts = _conflicts(sources)
    return EffectiveInstructionsProjectionV1(
        source_revision=discovery.source_revision,
        discovery_digest=discovery.discovery_digest,
        config_digest=config_digest,
        target_path=target,
        sources=tuple(sorted(sources, key=lambda item: item.source_id)),
        conflicts=conflicts,
        uncertainties=uncertainties,
        lens=lens,
        launch_ready=not uncertainties and not conflicts,
    )


def _project_source(
    source: DiscoveredProjectInstructionV1,
    *,
    target_path: str,
    selected_owners: frozenset[str] | None,
    selected_sources: frozenset[str],
    revisions: Mapping[str, str],
    expected_revisions: Mapping[str, str],
) -> EffectiveInstructionSourceV1:
    revision = revisions.get(source.materialization_owner)
    expected_revision = expected_revisions.get(source.materialization_owner)
    freshness = _freshness(revision, expected_revision)
    disposition, reason = _disposition(
        source,
        target_path=target_path,
        selected_owners=selected_owners,
        selected_sources=selected_sources,
        freshness=freshness,
    )
    return EffectiveInstructionSourceV1(
        source_id=source.source_id,
        selector_id=source.selector_id,
        kind=source.kind,
        relative_path=source.relative_path,
        scope_path=source.scope_path,
        source_digest=source.source_digest,
        size_bytes=source.size_bytes,
        materialization_owner=source.materialization_owner,
        materialization_revision=revision,
        freshness=freshness,
        disposition=disposition,
        reason=reason,
        precedence=_owner_precedence(source),
        token_estimate=(source.size_bytes + 3) // 4,
    )


def _disposition(
    source: DiscoveredProjectInstructionV1,
    *,
    target_path: str,
    selected_owners: frozenset[str] | None,
    selected_sources: frozenset[str],
    freshness: ContextFreshness,
) -> tuple[ContextDisposition, InclusionReason | OmissionReason]:
    if freshness is ContextFreshness.STALE:
        return ContextDisposition.OMIT, OmissionReason.STALE
    if (
        selected_owners is not None
        and source.materialization_owner not in selected_owners
    ):
        return ContextDisposition.OMIT, OmissionReason.UNSUPPORTED
    if not _scope_applies(source, target_path):
        return ContextDisposition.OMIT, OmissionReason.EXCLUDED_BY_POLICY
    if (
        source.kind
        in {
            ProjectInstructionKind.PROJECT_PROMPT,
            ProjectInstructionKind.PROJECT_SELECTOR,
        }
        and source.source_id not in selected_sources
    ):
        return ContextDisposition.OMIT, OmissionReason.EXCLUDED_BY_POLICY
    if source.kind is ProjectInstructionKind.AGENT_INSTRUCTION:
        return ContextDisposition.INCLUDE, InclusionReason.MANDATORY_INSTRUCTION
    if source.kind in {
        ProjectInstructionKind.PROJECT_PROMPT,
        ProjectInstructionKind.PROJECT_SELECTOR,
    }:
        return ContextDisposition.INCLUDE, InclusionReason.USER_SELECTION
    return ContextDisposition.INCLUDE, InclusionReason.PROJECT_RULE


def _freshness(
    revision: str | None,
    expected_revision: str | None,
) -> ContextFreshness:
    if expected_revision is not None and revision != expected_revision:
        return (
            ContextFreshness.STALE if revision is not None else ContextFreshness.UNKNOWN
        )
    return (
        ContextFreshness.CURRENT if revision is not None else ContextFreshness.UNKNOWN
    )


def _owner_precedence(source: DiscoveredProjectInstructionV1) -> int | None:
    if source.selector_id == "agent.agents_md":
        return len(PurePosixPath(source.scope_path).parts) if source.scope_path else 0
    if source.materialization_owner == "gigaloom_projects":
        return 0
    return None


def _scope_applies(source: DiscoveredProjectInstructionV1, target_path: str) -> bool:
    if source.scope is ProjectInstructionScope.PROJECT or not source.scope_path:
        return True
    target = PurePosixPath(target_path)
    scope = PurePosixPath(source.scope_path)
    return target == scope or scope in target.parents


def _lens_source(source: EffectiveInstructionSourceV1) -> ContextSourceDescriptor:
    included = source.disposition is ContextDisposition.INCLUDE
    return ContextSourceDescriptor(
        source_id=source.source_id,
        kind=ContextEntryKind.INSTRUCTION,
        source_digest=source.source_digest,
        disposition=source.disposition,
        freshness=source.freshness,
        inclusion_reason=source.reason if included else None,
        omission_reason=None if included else source.reason,
        relative_path=source.relative_path,
        size_bytes=source.size_bytes,
        protected=(included and source.reason is InclusionReason.MANDATORY_INSTRUCTION),
        token_count=source.token_estimate if included else None,
        token_method=(
            TokenEstimateMethod.HEURISTIC
            if included
            else TokenEstimateMethod.UNAVAILABLE
        ),
        token_confidence=(
            TokenEstimateConfidence.ESTIMATED
            if included
            else TokenEstimateConfidence.UNKNOWN
        ),
    )


def _uncertainties(
    discovery: ProjectInstructionDiscoveryV1,
    sources: tuple[EffectiveInstructionSourceV1, ...],
    *,
    revisions: Mapping[str, str],
    expected_revisions: Mapping[str, str],
) -> tuple[EffectiveInstructionUncertaintyV1, ...]:
    items: list[EffectiveInstructionUncertaintyV1] = []
    if discovery.scanned_paths_truncated:
        items.append(
            EffectiveInstructionUncertaintyV1(
                kind=InstructionUncertaintyKind.DISCOVERY_TRUNCATED,
                reason="git_visible_path_limit_reached",
            )
        )
    for omission in discovery.omissions:
        items.append(
            EffectiveInstructionUncertaintyV1(
                kind=InstructionUncertaintyKind.SOURCE_OMITTED,
                reason=omission.reason.value,
                materialization_owner=omission.selector_id,
            )
        )
    relevant = tuple(
        item
        for item in sources
        if item.reason
        not in {OmissionReason.EXCLUDED_BY_POLICY, OmissionReason.UNSUPPORTED}
    )
    included = tuple(
        item for item in sources if item.disposition is ContextDisposition.INCLUDE
    )
    for owner in sorted({item.materialization_owner for item in relevant}):
        if owner not in revisions:
            items.append(
                EffectiveInstructionUncertaintyV1(
                    kind=InstructionUncertaintyKind.ADAPTER_REVISION_UNKNOWN,
                    reason="materialization_revision_unavailable",
                    materialization_owner=owner,
                )
            )
        elif (
            owner in expected_revisions
            and revisions[owner] != expected_revisions[owner]
        ):
            items.append(
                EffectiveInstructionUncertaintyV1(
                    kind=InstructionUncertaintyKind.STALE_ADAPTER_REVISION,
                    reason="materialization_revision_mismatch",
                    materialization_owner=owner,
                )
            )
    for source in included:
        if source.precedence is None:
            items.append(
                EffectiveInstructionUncertaintyV1(
                    kind=InstructionUncertaintyKind.OWNER_PRECEDENCE_UNKNOWN,
                    reason="adapter_did_not_report_precedence",
                    source_id=source.source_id,
                    materialization_owner=source.materialization_owner,
                )
            )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.kind.value,
                item.materialization_owner or "",
                item.source_id or "",
                item.reason,
            ),
        )
    )


def _conflicts(
    sources: tuple[EffectiveInstructionSourceV1, ...],
) -> tuple[EffectiveInstructionConflictV1, ...]:
    included = tuple(
        item for item in sources if item.disposition is ContextDisposition.INCLUDE
    )
    conflicts: list[EffectiveInstructionConflictV1] = []
    for index, left in enumerate(included):
        for right in included[index + 1 :]:
            if not _scopes_overlap(left.scope_path, right.scope_path):
                continue
            owners = tuple(
                sorted({left.materialization_owner, right.materialization_owner})
            )
            if len(owners) > 1:
                conflicts.append(
                    EffectiveInstructionConflictV1(
                        kind=InstructionConflictKind.CROSS_OWNER_SCOPE_OVERLAP,
                        source_ids=tuple(sorted((left.source_id, right.source_id))),
                        materialization_owners=owners,
                        resolution="owner_specific_precedence_no_auto_merge",
                    )
                )
            elif left.precedence is None or left.precedence == right.precedence:
                conflicts.append(
                    EffectiveInstructionConflictV1(
                        kind=InstructionConflictKind.OWNER_PRECEDENCE_AMBIGUOUS,
                        source_ids=tuple(sorted((left.source_id, right.source_id))),
                        materialization_owners=owners,
                        resolution="adapter_review_required",
                    )
                )
    return tuple(sorted(conflicts, key=lambda item: (item.kind.value, item.source_ids)))


def _scopes_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return True
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return (
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )


def _normalize_target_path(value: str) -> str:
    if not value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(value) > 256 or "\x00" in value:
        raise ValueError("target_path must stay within the project root")
    return path.as_posix()


def _optional_identifier_set(values: Iterable[str] | None) -> frozenset[str] | None:
    return None if values is None else _identifier_set(values, "owner")


def _identifier_set(values: Iterable[str], field_name: str) -> frozenset[str]:
    normalized = frozenset(values)
    if any(
        not value or len(value) > 256 or any(char in value for char in "\x00\r\n")
        for value in normalized
    ):
        raise ValueError(f"{field_name} values must be 1..256 safe characters")
    return normalized


def _revision_map(values: Mapping[str, str] | None) -> dict[str, str]:
    revisions = dict(values or {})
    _identifier_set(revisions, "materialization owner")
    _identifier_set(revisions.values(), "materialization revision")
    return revisions


def _config_digest(
    *,
    discovery: ProjectInstructionDiscoveryV1,
    target_path: str,
    selected_owners: frozenset[str] | None,
    selected_sources: frozenset[str],
    revisions: Mapping[str, str],
    expected_revisions: Mapping[str, str],
) -> str:
    payload = {
        "format": EFFECTIVE_INSTRUCTIONS_FORMAT,
        "discovery_digest": discovery.discovery_digest,
        "target_path": target_path,
        "selected_owners": (
            None if selected_owners is None else sorted(selected_owners)
        ),
        "selected_sources": sorted(selected_sources),
        "materialization_revisions": dict(sorted(revisions.items())),
        "expected_materialization_revisions": dict(sorted(expected_revisions.items())),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "EFFECTIVE_INSTRUCTIONS_FORMAT",
    "EffectiveInstructionConflictV1",
    "EffectiveInstructionSourceV1",
    "EffectiveInstructionUncertaintyV1",
    "EffectiveInstructionsProjectionV1",
    "InstructionConflictKind",
    "InstructionUncertaintyKind",
    "compile_effective_instructions",
]
