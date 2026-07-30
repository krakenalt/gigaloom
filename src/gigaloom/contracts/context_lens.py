"""Pure compiler for the content-free Context Lens projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from gigaloom.contracts.context import (
    CompactionBoundary,
    ContextEntry,
    ContextEntryKind,
    ContextFreshness,
    ContextManifest,
    ContextOmission,
    ContextOverride,
    InclusionReason,
    OmissionReason,
    ProviderManagedUnknown,
    TokenEstimate,
    TokenEstimateConfidence,
    TokenEstimateMethod,
    build_context_manifest,
)


class ContextDisposition(str, Enum):
    """Requested treatment of an observable context source."""

    INCLUDE = "include"
    OMIT = "omit"


@dataclass(frozen=True)
class ContextSourceDescriptor:
    """Owner-supplied, content-free descriptor consumed by the Lens compiler."""

    source_id: str
    kind: ContextEntryKind
    source_digest: str
    disposition: ContextDisposition
    freshness: ContextFreshness
    inclusion_reason: InclusionReason | None = None
    omission_reason: OmissionReason | None = None
    relative_path: str | None = None
    symbol: str | None = None
    size_bytes: int | None = None
    protected: bool = False
    token_count: int | None = None
    token_method: TokenEstimateMethod = TokenEstimateMethod.UNAVAILABLE
    token_confidence: TokenEstimateConfidence = TokenEstimateConfidence.UNKNOWN

    def __post_init__(self) -> None:
        if self.disposition is ContextDisposition.INCLUDE:
            if self.inclusion_reason is None or self.omission_reason is not None:
                raise ValueError("included source requires only an inclusion_reason")
            if self.freshness is ContextFreshness.STALE:
                raise ValueError("stale source cannot be included")
        elif self.omission_reason is None or self.inclusion_reason is not None:
            raise ValueError("omitted source requires only an omission_reason")

        if self.protected:
            if self.kind is not ContextEntryKind.INSTRUCTION:
                raise ValueError("only instructions may be protected")
            if self.disposition is ContextDisposition.OMIT:
                raise ProtectedContextSourceError(
                    f"protected instruction cannot be omitted: {self.source_id}"
                )
            if self.inclusion_reason is not InclusionReason.MANDATORY_INSTRUCTION:
                raise ValueError("protected instruction must use mandatory_instruction")

        # Reuse the manifest contracts as the single validation source.
        if self.disposition is ContextDisposition.INCLUDE:
            self.as_entry()
            self.as_token_estimate()
        else:
            self.as_omission()

    def as_entry(self) -> ContextEntry:
        """Convert an included descriptor to its manifest entry."""
        if self.inclusion_reason is None:
            raise ValueError("source is not configured for inclusion")
        return ContextEntry(
            entry_id=self.source_id,
            kind=self.kind,
            source_digest=self.source_digest,
            inclusion_reason=self.inclusion_reason,
            relative_path=self.relative_path,
            symbol=self.symbol,
            size_bytes=self.size_bytes,
            freshness=self.freshness,
        )

    def as_omission(self) -> ContextOmission:
        """Convert an omitted descriptor to its visible omission."""
        if self.omission_reason is None:
            raise ValueError("source is not configured for omission")
        return ContextOmission(
            source_id=self.source_id,
            source_kind=self.kind,
            reason=self.omission_reason,
            source_digest=self.source_digest,
        )

    def as_token_estimate(self) -> TokenEstimate:
        """Convert an included descriptor to its labeled token estimate."""
        return TokenEstimate(
            scope_id=self.source_id,
            token_count=self.token_count,
            method=self.token_method,
            confidence=self.token_confidence,
        )


class ProtectedContextSourceError(ValueError):
    """Raised when a projection attempts to omit a protected instruction."""


@dataclass(frozen=True)
class ContextLensTokenSummary:
    """Aggregate without pretending unknown scopes are zero."""

    known_token_count: int
    known_scope_count: int
    unknown_scope_count: int
    methods: tuple[TokenEstimateMethod, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a stable UI-facing representation."""
        return {
            "known_token_count": self.known_token_count,
            "known_scope_count": self.known_scope_count,
            "unknown_scope_count": self.unknown_scope_count,
            "methods": [item.value for item in self.methods],
        }


@dataclass(frozen=True)
class ContextLensProjection:
    """Read-only projection of an immutable ContextManifest."""

    manifest: ContextManifest
    token_summary: ContextLensTokenSummary

    @property
    def is_partial(self) -> bool:
        """Return whether observable omissions or provider unknowns exist."""
        return bool(self.manifest.omissions or self.manifest.provider_managed_unknowns)

    def to_dict(self) -> dict[str, Any]:
        """Return the projection without source content or prompt material."""
        return {
            "manifest": self.manifest.to_dict(),
            "token_summary": self.token_summary.to_dict(),
            "is_partial": self.is_partial,
        }


def compile_context_lens(
    *,
    source_revision: str,
    config_digest: str,
    sources: Iterable[ContextSourceDescriptor],
    overrides: Iterable[ContextOverride] = (),
    compaction_boundaries: Iterable[CompactionBoundary] = (),
    provider_managed_unknowns: Iterable[ProviderManagedUnknown] = (),
) -> ContextLensProjection:
    """Compile owner-supplied descriptors into one deterministic Lens."""
    source_items = tuple(sources)
    _require_unique_source_ids(source_items)

    entries = tuple(
        source.as_entry()
        for source in source_items
        if source.disposition is ContextDisposition.INCLUDE
    )
    omissions = tuple(
        source.as_omission()
        for source in source_items
        if source.disposition is ContextDisposition.OMIT
    )
    estimates = tuple(
        source.as_token_estimate()
        for source in source_items
        if source.disposition is ContextDisposition.INCLUDE
    )
    manifest = build_context_manifest(
        source_revision=source_revision,
        config_digest=config_digest,
        entries=entries,
        omissions=omissions,
        overrides=overrides,
        compaction_boundaries=compaction_boundaries,
        token_estimates=estimates,
        provider_managed_unknowns=provider_managed_unknowns,
    )
    return ContextLensProjection(
        manifest=manifest,
        token_summary=_summarize_tokens(manifest.token_estimates),
    )


def _require_unique_source_ids(
    sources: tuple[ContextSourceDescriptor, ...],
) -> None:
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("context source ids must be unique")


def _summarize_tokens(
    estimates: tuple[TokenEstimate, ...],
) -> ContextLensTokenSummary:
    known = tuple(
        estimate for estimate in estimates if estimate.token_count is not None
    )
    return ContextLensTokenSummary(
        known_token_count=sum(estimate.token_count or 0 for estimate in known),
        known_scope_count=len(known),
        unknown_scope_count=len(estimates) - len(known),
        methods=tuple(
            sorted({item.method for item in estimates}, key=lambda item: item.value)
        ),
    )


__all__ = [
    "ContextDisposition",
    "ContextLensProjection",
    "ContextLensTokenSummary",
    "ContextSourceDescriptor",
    "ProtectedContextSourceError",
    "compile_context_lens",
]
