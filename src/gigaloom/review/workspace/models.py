"""Typed, content-free models for one run's evidence workspace."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


EVIDENCE_WORKSPACE_SCHEMA_VERSION = 1
EVIDENCE_WORKSPACE_KIND = "gigaloom.operator_evidence_workspace.v1"
MAX_EVIDENCE_REFERENCES = 256
MAX_CHANGED_FILES = 500
MAX_NEXT_ACTIONS = 100


class EvidenceSection(StrEnum):
    """Closed workspace sections with an explicit availability contract."""

    CANDIDATE = "candidate"
    GATE = "gate"
    FINDINGS = "findings"
    CHANGE_SET = "change_set"
    CONTEXT = "context"
    IMPACT = "impact"
    TRUST_FLOWS = "trust_flows"
    COSTS = "costs"
    TERMINAL = "terminal"


class EvidenceFreshness(StrEnum):
    """Whether an owner-backed reference matches its advertised revision."""

    CURRENT = "current"
    STALE = "stale"


class EvidenceOmissionReason(StrEnum):
    """Truthful reasons why a workspace section has no owner-backed reference."""

    NOT_APPLICABLE = "not_applicable"
    NOT_RECORDED = "not_recorded"
    REDACTED = "redacted"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class EvidenceWorkspaceRun:
    """Identity and owner binding for one immutable run snapshot."""

    run_id: str
    session_id: str
    owner_id: str
    workspace_id: str
    status: str
    revision: str


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Stable reference to evidence retained by another authority."""

    section: EvidenceSection
    kind: str
    authority: str
    resource_id: str
    owner_id: str
    workspace_id: str
    revision: str
    sha256: str
    state: str
    freshness: EvidenceFreshness = EvidenceFreshness.CURRENT


@dataclass(frozen=True, slots=True)
class ChangeSetProjection:
    """Bounded relative paths and immutable digests owned by the project layer."""

    authority: str
    owner_id: str
    workspace_id: str
    revision: str
    base_sha256: str
    patch_sha256: str
    changed_files: tuple[str, ...] = ()
    truncated: bool = False
    freshness: EvidenceFreshness = EvidenceFreshness.CURRENT


@dataclass(frozen=True, slots=True)
class EvidenceOmission:
    """Explicit absence of a workspace section."""

    section: EvidenceSection
    reason: EvidenceOmissionReason
    authority: str


@dataclass(frozen=True, slots=True)
class NextActionReference:
    """Owner-bound action identity without a copied form payload or secret."""

    action_id: str
    kind: str
    authority: str
    owner_id: str
    workspace_id: str
    revision: str
    sha256: str
    consequence: str
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceWorkspaceProjection:
    """Canonical, digest-bound projection consumed by product surfaces."""

    run: EvidenceWorkspaceRun
    references: tuple[EvidenceReference, ...]
    change_set: ChangeSetProjection | None
    omissions: tuple[EvidenceOmission, ...]
    next_actions: tuple[NextActionReference, ...]
    projection_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return a detached canonical JSON-compatible document."""
        from .projection import evidence_workspace_to_dict

        return evidence_workspace_to_dict(self)

    @classmethod
    def from_dict(cls, payload: object) -> EvidenceWorkspaceProjection:
        """Parse and verify one exact schema-v1 workspace projection."""
        from .projection import evidence_workspace_from_dict

        return evidence_workspace_from_dict(payload)
