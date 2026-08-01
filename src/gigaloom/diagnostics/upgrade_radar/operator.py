"""Explicit operator composition for recommendation-only upgrade checks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
import re
from typing import Callable

from gigaloom.contracts import (
    CompatibilityObservationV1,
    OperationalEvidenceStatus,
    RouteEvidenceV1,
    UpgradeRadarReportV1,
    digest_command_tokens,
    upgrade_radar_report_to_dict,
)
from gigaloom.diagnostics.upgrade_radar.comparison import (
    CaseObservationV1,
    GateObservationV1,
    RouteEvaluationV1,
    compare_route_evaluations,
)
from gigaloom.diagnostics.upgrade_radar.comparison_codec import canonical_digest
from gigaloom.diagnostics.upgrade_radar.contracts import (
    RouteSnapshotV1,
    SealedCorpusV1,
)
from gigaloom.diagnostics.upgrade_radar.corpus import load_named_sealed_corpus
from gigaloom.diagnostics.upgrade_radar.policy import build_upgrade_report
from gigaloom.diagnostics.upgrade_radar.reports import (
    load_upgrade_report,
    save_upgrade_report,
)
from gigaloom.executables import ExecutableResolution, ExecutableResolver
from gigaloom.harnesses.agent_profiles import load_builtin_agent_profiles
from gigaloom.harnesses.agent_profiles.models import (
    AgentProfileV1,
    StructuredAgentRouteRef,
)
from gigaloom.native.codex_operator import (
    CodexCompatibilitySnapshot,
    probe_codex_compatibility,
)


UPGRADE_RADAR_REPORT_RELATIVE_PATH = Path("diagnostics") / "upgrade-radar"
MAX_UPGRADE_RADAR_REPORTS = 128
_REPORT_NAME = re.compile(r"upgrade-report-[0-9a-f]{24}\.json\Z")
_Probe = Callable[..., CodexCompatibilitySnapshot]
_Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class UpgradeRadarCheckResult:
    """One persisted comparison and its content-free operator projection."""

    agent_id: str
    corpus_id: str
    report: UpgradeRadarReportV1
    current_version: str | None
    candidate_version: str | None
    sealed_evaluation_complete: bool = False


class UpgradeRadarReportStore:
    """Bounded immutable report store shared by the explicit CLI and read-only Web."""

    def __init__(self, data_dir: str | Path) -> None:
        self._root = Path(data_dir) / UPGRADE_RADAR_REPORT_RELATIVE_PATH

    def save(self, report: UpgradeRadarReportV1) -> None:
        """Persist one immutable report without pruning prior evidence."""
        existing = self.list()
        if len(existing) >= MAX_UPGRADE_RADAR_REPORTS and all(
            item.report_id != report.report_id for item in existing
        ):
            raise ValueError("upgrade report store reached its bounded capacity")
        save_upgrade_report(self._root / f"{report.report_id}.json", report)

    def list(self) -> tuple[UpgradeRadarReportV1, ...]:
        """Load every admitted report in newest-evidence order."""
        if not self._root.exists():
            return ()
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("upgrade report store must be a regular directory")
        candidates = tuple(islice(self._root.iterdir(), MAX_UPGRADE_RADAR_REPORTS + 1))
        if len(candidates) > MAX_UPGRADE_RADAR_REPORTS:
            raise ValueError("upgrade report store exceeds its bounded capacity")
        reports: list[UpgradeRadarReportV1] = []
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("upgrade report store contains an unsafe entry")
            if _REPORT_NAME.fullmatch(candidate.name) is None:
                raise ValueError("upgrade report store contains an unknown entry")
            reports.append(load_upgrade_report(candidate))
        return tuple(
            sorted(
                reports,
                key=lambda item: (
                    _observation_for(item, candidate=True).observed_at,
                    item.report_id,
                ),
                reverse=True,
            )
        )


class UpgradeRadarService:
    """Compare one installed Codex route with an explicit candidate revision."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        probe: _Probe = probe_codex_compatibility,
        clock: _Clock | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._store = UpgradeRadarReportStore(data_dir)
        self._probe = probe
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(
        self,
        agent_id: str,
        *,
        candidate_command: str,
        corpus_id: str,
    ) -> UpgradeRadarCheckResult:
        """Probe two revisions, compare evidence, and persist no-action authority."""
        profile, route = _supported_route(agent_id)
        corpus = load_named_sealed_corpus(corpus_id)
        current = self._current_resolution(route)
        candidate = _candidate_resolution(route, candidate_command)
        if _same_executable(current, candidate):
            raise ValueError("candidate command matches the installed revision")
        observed_at = self._now()
        current_probe = self._probe(
            current.command,
            profile_digest=profile.profile_digest,
            allow_compatible_unverified=False,
            now=observed_at,
        )
        candidate_probe = self._probe(
            candidate.command,
            profile_digest=profile.profile_digest,
            allow_compatible_unverified=False,
            now=observed_at,
        )
        current_observation = _route_observation(current_probe, route.route_id)
        candidate_observation = _route_observation(candidate_probe, route.route_id)
        if not current_observation.native_eligible:
            raise ValueError("installed agent command could not be observed")
        if (
            current_observation.executable_identity
            == candidate_observation.executable_identity
        ):
            raise ValueError("candidate command matches the installed revision")
        current_evaluation = _unknown_evaluation(
            corpus,
            _route_snapshot(
                profile,
                route,
                current.command,
                current_observation,
                label="installed",
            ),
        )
        candidate_evaluation = _unknown_evaluation(
            corpus,
            _route_snapshot(
                profile,
                route,
                candidate.command,
                candidate_observation,
                label="candidate",
            ),
        )
        comparison = compare_route_evaluations(
            corpus,
            current_evaluation,
            candidate_evaluation,
        )
        report = build_upgrade_report(
            corpus,
            current_evaluation,
            candidate_evaluation,
            comparison,
        )
        self._store.save(report)
        return UpgradeRadarCheckResult(
            agent_id=profile.agent_id,
            corpus_id=corpus.corpus_id,
            report=report,
            current_version=current_observation.reported_version,
            candidate_version=candidate_observation.reported_version,
        )

    def _current_resolution(
        self,
        route: StructuredAgentRouteRef,
    ) -> ExecutableResolution:
        command_ref = route.command_ref
        if command_ref is None:
            raise ValueError("installed agent route has no executable command")
        resolution = ExecutableResolver.from_user_config(
            self._data_dir / "config.toml"
        ).resolve(route.harness_id, command_ref.executable_name)
        if not resolution.available:
            raise ValueError("installed agent command is unavailable")
        return resolution

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("upgrade radar clock must be timezone-aware")
        return value


def upgrade_radar_check_to_dict(result: UpgradeRadarCheckResult) -> dict[str, object]:
    """Serialize one CLI result without executable paths or action authority."""
    return {
        "schema_version": 1,
        "kind": "gigaloom_upgrade_radar_check",
        "agent_id": result.agent_id,
        "corpus_id": result.corpus_id,
        "sealed_evaluation_complete": result.sealed_evaluation_complete,
        "recommendation_only": True,
        "action_authorized": False,
        "automatic_apply": False,
        "candidate_probe_scope": "isolated_metadata_and_schema",
        "installation_performed": False,
        "update_performed": False,
        "content_free": True,
        "current_version": result.current_version,
        "candidate_version": result.candidate_version,
        "report": upgrade_radar_report_to_dict(result.report),
    }


def upgrade_radar_report_list_to_dict(
    reports: tuple[UpgradeRadarReportV1, ...],
) -> dict[str, object]:
    """Serialize a bounded read-only Web list with no executable identities."""
    return {
        "schema_version": 1,
        "kind": "gigaloom_upgrade_radar_reports",
        "recommendation_only": True,
        "action_authorized": False,
        "content_free": True,
        "reports": [_report_summary(item) for item in reports],
    }


def _supported_route(
    agent_id: str,
) -> tuple[AgentProfileV1, StructuredAgentRouteRef]:
    if agent_id != "codex":
        raise ValueError("upgrade radar currently supports only the codex agent")
    profile = next(
        item for item in load_builtin_agent_profiles() if item.agent_id == agent_id
    )
    route = next(
        (
            item
            for item in profile.structured_routes
            if item.route_id == "codex.app-server"
        ),
        None,
    )
    if route is None:
        raise ValueError("codex structured route is unavailable")
    return profile, route


def _candidate_resolution(
    route: StructuredAgentRouteRef,
    candidate_command: str,
) -> ExecutableResolution:
    command_ref = route.command_ref
    if command_ref is None:
        raise ValueError("candidate route has no executable command")
    resolution = ExecutableResolver({route.harness_id: candidate_command}).resolve(
        route.harness_id, command_ref.executable_name
    )
    if not resolution.available:
        raise ValueError("candidate command must be an absolute executable path")
    return resolution


def _same_executable(
    current: ExecutableResolution,
    candidate: ExecutableResolution,
) -> bool:
    if current.executable is None or candidate.executable is None:
        return False
    try:
        return (
            Path(current.executable)
            .resolve(strict=True)
            .samefile(Path(candidate.executable).resolve(strict=True))
        )
    except OSError:
        return False


def _route_observation(
    snapshot: CodexCompatibilitySnapshot,
    route_id: str,
) -> CompatibilityObservationV1:
    if snapshot.observation is None:
        raise ValueError("agent compatibility probe returned no observation")
    return replace(snapshot.observation, route_id=route_id)


def _route_snapshot(
    profile: AgentProfileV1,
    route: StructuredAgentRouteRef,
    command: tuple[str, ...],
    observation: CompatibilityObservationV1,
    *,
    label: str,
) -> RouteSnapshotV1:
    command_digest = digest_command_tokens(command)
    revision_digest = canonical_digest(
        {
            "agent_id": profile.agent_id,
            "route_id": route.route_id,
            "profile_digest": profile.profile_digest,
            "executable_identity": observation.executable_identity,
            "reported_version": observation.reported_version,
            "protocol_handshake_digest": observation.protocol.handshake_digest,
            "capability_fingerprint": observation.capability_fingerprint,
        }
    )
    return RouteSnapshotV1(
        route=RouteEvidenceV1(
            route_id=route.route_id,
            revision_digest=revision_digest,
            capability_fingerprint=observation.capability_fingerprint,
            compatibility_observation_digest=observation.probe_digest,
        ),
        compatibility=observation,
        command_tokens_digest=command_digest,
        model_identity=f"{profile.agent_id}:{label}",
    )


def _unknown_evaluation(
    corpus: SealedCorpusV1,
    route: RouteSnapshotV1,
) -> RouteEvaluationV1:
    supported = tuple(
        sorted(
            set(route.compatibility.required_capabilities)
            - set(route.compatibility.missing_capabilities)
        )
    )
    return RouteEvaluationV1(
        route=route,
        sealed_corpus_digest=corpus.sealed_digest,
        supported_capabilities=supported,
        cases=tuple(
            CaseObservationV1(
                case_id=case.case_id,
                gates=tuple(
                    GateObservationV1(
                        gate_id=gate_id,
                        status=OperationalEvidenceStatus.UNKNOWN,
                        evidence_digest=canonical_digest(
                            {
                                "route_snapshot_digest": route.snapshot_digest,
                                "case_id": case.case_id,
                                "gate_id": gate_id,
                                "status": "unknown",
                            }
                        ),
                    )
                    for gate_id in case.required_gates
                ),
                latency_ms=None,
                input_tokens=None,
                output_tokens=None,
                known_cost_microunits=None,
                omissions=(
                    "known_cost_not_observed",
                    "latency_not_observed",
                    "sealed_case_execution_not_available",
                    "usage_not_observed",
                ),
            )
            for case in corpus.cases
        ),
    )


def _observation_for(
    report: UpgradeRadarReportV1,
    *,
    candidate: bool,
) -> CompatibilityObservationV1:
    route = report.candidate_route if candidate else report.current_route
    return next(
        item
        for item in report.compatibility_observations
        if item.probe_digest == route.compatibility_observation_digest
    )


def _report_summary(report: UpgradeRadarReportV1) -> dict[str, object]:
    current = _observation_for(report, candidate=False)
    candidate = _observation_for(report, candidate=True)
    return {
        "report_id": report.report_id,
        "sealed_corpus_digest": report.sealed_corpus_digest,
        "observed_at": candidate.observed_at.isoformat(),
        "recommendation": report.recommendation.value,
        "current": {
            "route_id": report.current_route.route_id,
            "revision_digest": report.current_route.revision_digest,
            "version": current.reported_version,
            "compatibility_status": current.status.value,
        },
        "candidate": {
            "route_id": report.candidate_route.route_id,
            "revision_digest": report.candidate_route.revision_digest,
            "version": candidate.reported_version,
            "compatibility_status": candidate.status.value,
        },
        "uncertainty": list(report.uncertainty),
        "omissions": list(report.omissions),
        "content_free": report.content_free,
        "recommendation_only": True,
        "action_authorized": False,
    }


__all__ = [
    "MAX_UPGRADE_RADAR_REPORTS",
    "UPGRADE_RADAR_REPORT_RELATIVE_PATH",
    "UpgradeRadarCheckResult",
    "UpgradeRadarReportStore",
    "UpgradeRadarService",
    "upgrade_radar_check_to_dict",
    "upgrade_radar_report_list_to_dict",
]
