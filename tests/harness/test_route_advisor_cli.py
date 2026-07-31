"""B3 CLI composition tests for governed route decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from gigaloom.cli_commands.commands import route_advisor as route_commands
from gigaloom.cli_commands.handlers.route_advisor import (
    RouteCommandHandlers,
    RouteCommandService,
)
from gigaloom.config import HarnessConfig
from gigaloom.execution.api import (
    CapabilityEvidence,
    CompatibilityGrade,
    RouteAdvisorApplicationService,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteIntent,
    RouteRecommendationQueryV1,
    RouteRecommendationSnapshotV1,
    RouteRequirementsV1,
    StructuredRouteCandidateV1,
)
from gigaloom.review.api import (
    RouteDecisionOverrideError,
    RouteDecisionRepository,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


class _FixtureSource:
    def __init__(self, *, drift: bool = False) -> None:
        self.drift = drift
        self.queries: list[RouteRecommendationQueryV1] = []

    def snapshot(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteRecommendationSnapshotV1:
        self.queries.append(query)
        requirements = RouteRequirementsV1(
            intent=query.intent,
            required_capabilities=("structured_prompt",),
            required_transport_classes=("acp_stdio_v1",),
            workspace_policy=query.workspace_policy,
            network_policy=query.network_policy,
            cost_policy_ref=query.cost_policy_ref,
            platform=query.platform,
            context_manifest_digest=query.context_manifest_digest,
            project_id="prj_drifted" if self.drift else query.project_id,
            launch_profile_digest=None,
            preferred_route_id=query.preferred_route_id,
            required_host_id=query.required_host_id,
            required_account_digest=query.required_account_digest,
            require_known_cost=query.require_known_cost,
            require_sealed_evaluation=query.require_sealed_evaluation,
            require_session_portability=query.require_session_portability,
        )
        return RouteRecommendationSnapshotV1(
            requirements=requirements,
            candidates=(_candidate("agent-a.acp", 0), _candidate("agent-b.acp", 1)),
            project_catalog_digest=DIGEST_A,
            capability_catalog_digest=DIGEST_B,
            cost_policy_digest=DIGEST_C,
        )


def test_route_cli_recommends_shows_and_overrides_without_execution(
    tmp_path,
    capsys,
) -> None:
    parser = argparse.ArgumentParser(prog="giga")
    subparsers = parser.add_subparsers(dest="command")
    route_commands.register(subparsers, argparse.ArgumentParser(add_help=False))
    source = _FixtureSource()
    service = RouteCommandService(
        advisor=RouteAdvisorApplicationService(source),
        repository=RouteDecisionRepository(tmp_path / "route-decisions"),
        clock=lambda: NOW,
    )
    handlers = RouteCommandHandlers(service)
    config = HarnessConfig(data_dir=tmp_path)

    recommend = parser.parse_args(
        [
            "route",
            "recommend",
            "--project",
            "prj_fixture",
            "--intent",
            "read",
            "--task-digest",
            DIGEST_A,
            "--context-manifest-digest",
            DIGEST_B,
            "--capability",
            "structured_prompt",
            "--transport",
            "acp_stdio_v1",
            "--platform",
            "linux",
        ]
    )
    assert recommend.handler == "_handle_route_recommend"
    assert handlers.recommend(recommend, config) == 0
    first_output = capsys.readouterr().out
    assert "Recommended route: agent-a.acp" in first_output
    assert "Execution started: no" in first_output
    first = next((tmp_path / "route-decisions").glob("route_*.json"))
    receipt_id = first.stem

    show = parser.parse_args(["route", "show", receipt_id, "--json"])
    assert handlers.show(show, config) == 0
    assert f'"route_decision_id": "{receipt_id}"' in capsys.readouterr().out

    override = parser.parse_args(
        [
            "route",
            "override",
            receipt_id,
            "--route",
            "agent-b.acp",
            "--reason",
            "operator_selected",
        ]
    )
    assert handlers.override(override, config) == 0
    override_output = capsys.readouterr().out
    assert "Recommended route: agent-b.acp" in override_output
    assert "Execution started: no" in override_output
    assert len(tuple((tmp_path / "route-decisions").glob("route_*.json"))) == 2
    assert len(source.queries) == 1


def test_route_cli_rejects_ineligible_override_and_drifted_snapshot(tmp_path) -> None:
    repository = RouteDecisionRepository(tmp_path / "route-decisions")
    service = RouteCommandService(
        advisor=RouteAdvisorApplicationService(_FixtureSource()),
        repository=repository,
        clock=lambda: NOW,
    )
    query = _query()
    receipt = service.recommend(query)

    with pytest.raises(RouteDecisionOverrideError, match="not eligible"):
        service.override(
            receipt.route_decision_id,
            route_id="agent-c.acp",
            reason_code="operator_selected",
        )
    with pytest.raises(ValueError, match="project_id"):
        RouteAdvisorApplicationService(_FixtureSource(drift=True)).recommend(query)


def _query() -> RouteRecommendationQueryV1:
    return RouteRecommendationQueryV1(
        project_id="prj_fixture",
        intent=RouteIntent.READ,
        task_digest=DIGEST_A,
        context_manifest_digest=DIGEST_B,
        required_capabilities=("structured_prompt",),
        required_transport_classes=("acp_stdio_v1",),
        workspace_policy="read_only",
        network_policy="denied",
        cost_policy_ref="explicit_unlimited",
        platform="linux",
    )


def _candidate(route_id: str, priority: int) -> StructuredRouteCandidateV1:
    satisfied = RouteFactState.SATISFIED
    suffix = "d" if priority == 0 else "e"
    return StructuredRouteCandidateV1(
        route_id=route_id,
        agent_id=route_id.split(".", maxsplit=1)[0],
        profile_digest=suffix * 64,
        transport_class="acp_stdio_v1",
        capabilities=(CapabilityEvidence("structured_prompt", satisfied),),
        platform_support=("linux",),
        workspace_policies=("read_only",),
        network_policies=("denied",),
        profile_admission=satisfied,
        route_presence=satisfied,
        executable_readiness=satisfied,
        version_readiness=satisfied,
        capability_snapshot_state=satisfied,
        capability_snapshot_digest=("f" if priority == 0 else "1") * 64,
        account_state=satisfied,
        account_digest=("2" if priority == 0 else "3") * 64,
        policy_state=satisfied,
        budget_state=satisfied,
        project_location_state=satisfied,
        sealed_evaluation_state=RouteFactState.UNKNOWN,
        session_portability_state=RouteFactState.UNKNOWN,
        cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
        compatibility_grade=CompatibilityGrade.READY,
        policy_priority=priority,
    )
