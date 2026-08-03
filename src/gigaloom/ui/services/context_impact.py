"""Read-only application ports for Context Lens and Impact Radar APIs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from gigaloom.execution.api import NativeCodexContextProjection
from gigaloom.projects import api as projects_api


class ContextProjectionQuery(Protocol):
    """Owner-scoped source of one native session Context Lens projection."""

    def get_native_codex_context(
        self,
        *,
        session_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> NativeCodexContextProjection: ...


@dataclass(frozen=True)
class ImpactProjectionOutcome:
    """One advisory projection and whether its exact index was retained."""

    projection: projects_api.PythonImpactResult
    cache_hit: bool


class StaleEffectiveInstructionsProjectionError(LookupError):
    """Raised when detail no longer matches a caller-observed discovery."""


class ImpactProjectionService:
    """Compile bounded read-only project impact and instruction projections."""

    def __init__(
        self,
        cache: projects_api.PythonImpactIndexCache | None = None,
    ) -> None:
        self.cache = cache or projects_api.PythonImpactIndexCache()

    def project(
        self,
        *,
        workspace: str,
        changed_paths: Iterable[str],
        expected_index_digest: str | None = None,
        expected_source_revision: str | None = None,
    ) -> ImpactProjectionOutcome:
        """Return a cold current index or one exact caller-bound warm snapshot."""
        root = Path(workspace).expanduser().resolve()
        if (expected_index_digest is None) != (expected_source_revision is None):
            raise ValueError(
                "index_digest and source_revision must be supplied together"
            )
        if expected_index_digest is None:
            index = projects_api.compile_python_impact_index(root)
            self.cache.put(root, index)
            cache_hit = False
        else:
            assert expected_source_revision is not None
            index = self.cache.get(
                root,
                index_digest=expected_index_digest,
                source_revision=expected_source_revision,
            )
            cache_hit = True
        return ImpactProjectionOutcome(
            projection=projects_api.project_python_impact(index, changed_paths),
            cache_hit=cache_hit,
        )

    def effective_instructions(
        self,
        *,
        workspace: str,
        target_path: str = "",
        selected_materialization_owners: Iterable[str] | None = None,
        selected_source_ids: Iterable[str] = (),
        materialization_revisions: dict[str, str] | None = None,
        expected_materialization_revisions: dict[str, str] | None = None,
        expected_discovery_digest: str | None = None,
    ) -> projects_api.instructions_api.EffectiveInstructionsProjectionV1:
        """Return a fresh content-free projection bound to optional prior digest."""
        root = Path(workspace).expanduser().resolve()
        discovery = projects_api.instructions_api.discover_project_instructions(root)
        if (
            expected_discovery_digest is not None
            and discovery.discovery_digest != expected_discovery_digest
        ):
            raise StaleEffectiveInstructionsProjectionError(
                "Effective Instructions discovery changed; resnapshot required"
            )
        return projects_api.instructions_api.compile_effective_instructions(
            discovery,
            target_path=target_path,
            selected_materialization_owners=selected_materialization_owners,
            selected_source_ids=selected_source_ids,
            materialization_revisions=materialization_revisions,
            expected_materialization_revisions=(expected_materialization_revisions),
        )


__all__ = [
    "ContextProjectionQuery",
    "ImpactProjectionOutcome",
    "ImpactProjectionService",
    "StaleEffectiveInstructionsProjectionError",
]
