"""CLI presentation for source-backed governed route decisions."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from gigaloom.config import HarnessConfig
from gigaloom.execution.api import (
    RouteAdvisorApplicationService,
    RouteIntent,
    LocalRouteRecommendationSource,
    RouteRecommendationQueryV1,
)
from gigaloom.review.api import (
    RouteDecisionBindingsV1,
    RouteDecisionReceiptV1,
    RouteDecisionRepository,
    create_route_decision_receipt,
    override_route_decision_receipt,
    route_decision_receipt_to_dict,
    verify_route_decision_receipt,
)
from gigaloom.harnesses.agent_profiles import (
    build_core_command_collision_contract,
    load_agent_profile_registry,
)


@dataclass(frozen=True)
class RouteCommandService:
    """Compose recommendation and immutable receipt owners for CLI/Web parity."""

    advisor: RouteAdvisorApplicationService
    repository: RouteDecisionRepository
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def recommend(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteDecisionReceiptV1:
        """Persist one deterministic recommendation without starting a run."""
        result = self.advisor.recommend(query)
        snapshot = result.snapshot
        receipt = create_route_decision_receipt(
            result.advice,
            RouteDecisionBindingsV1(
                task_digest=query.task_digest,
                context_manifest_digest=query.context_manifest_digest,
                project_catalog_digest=snapshot.project_catalog_digest,
                launch_profile_digest=snapshot.requirements.launch_profile_digest,
                capability_catalog_digest=snapshot.capability_catalog_digest,
                cost_policy_digest=snapshot.cost_policy_digest,
            ),
            created_at=_timestamp(self.clock()),
        )
        return self.repository.save(receipt)

    def show(self, route_decision_id: str) -> RouteDecisionReceiptV1:
        """Load and integrity-check one immutable decision."""
        return verify_route_decision_receipt(self.repository.get(route_decision_id))

    def override(
        self,
        route_decision_id: str,
        *,
        route_id: str,
        reason_code: str,
    ) -> RouteDecisionReceiptV1:
        """Persist a new eligible-only manual selection receipt."""
        receipt = override_route_decision_receipt(
            self.show(route_decision_id),
            route_id=route_id,
            reason_code=reason_code,
            created_at=_timestamp(self.clock()),
        )
        return self.repository.save(receipt)


@dataclass(frozen=True)
class RouteCommandHandlers:
    """Bound handlers installed by the final shared CLI composition owner."""

    service: RouteCommandService

    def recommend(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        receipt = self.service.recommend(route_query_from_args(args))
        _print_receipt(receipt, json_output=args.json)
        return 0 if receipt.recommended_route_id is not None else 1

    def show(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        _print_receipt(
            self.service.show(args.route_decision_id),
            json_output=args.json,
        )
        return 0

    def override(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        receipt = self.service.override(
            args.route_decision_id,
            route_id=args.selected_route_id,
            reason_code=args.reason,
        )
        _print_receipt(receipt, json_output=args.json)
        return 0


def _handle_route_recommend(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    return _default_handlers(config).recommend(args, config)


def _handle_route_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    return _default_handlers(config).show(args, config)


def _handle_route_override(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    return _default_handlers(config).override(args, config)


def build_local_route_source(config: HarnessConfig) -> LocalRouteRecommendationSource:
    """Build the shared local source from the canonical parser collision set."""
    from gigaloom.cli_commands.parser import build_parser

    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    choices = command_action.choices
    if choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    profiles = load_agent_profile_registry(
        config.data_dir,
        collision_contract=build_core_command_collision_contract(choices),
    )
    if profiles.issues:
        raise ValueError("agent registry has unresolved stale profiles")
    return LocalRouteRecommendationSource(config.data_dir, profiles.registry.profiles)


def _default_handlers(config: HarnessConfig) -> RouteCommandHandlers:
    service = RouteCommandService(
        advisor=RouteAdvisorApplicationService(build_local_route_source(config)),
        repository=RouteDecisionRepository(Path(config.data_dir) / "route-decisions"),
    )
    return RouteCommandHandlers(service)


def route_query_from_args(args: argparse.Namespace) -> RouteRecommendationQueryV1:
    """Build a deterministic query without reading prompt or provider content."""
    return RouteRecommendationQueryV1(
        project_id=args.project,
        intent=RouteIntent(args.intent),
        task_digest=args.task_digest,
        context_manifest_digest=args.context_manifest_digest,
        required_capabilities=_sorted_unique(args.capability),
        required_transport_classes=_sorted_unique(args.transport),
        workspace_policy=args.workspace_policy,
        network_policy=args.network_policy,
        cost_policy_ref=args.cost_policy,
        platform=args.platform or sys.platform,
        launch_profile_id=args.launch_profile,
        preferred_route_id=args.prefer_route,
        required_host_id=args.host,
        required_account_digest=args.account_digest,
        require_known_cost=args.require_known_cost,
        require_sealed_evaluation=args.require_sealed_evaluation,
        require_session_portability=args.require_session_portability,
    )


def _print_receipt(
    receipt: RouteDecisionReceiptV1,
    *,
    json_output: bool,
) -> None:
    payload = route_decision_receipt_to_dict(receipt)
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    print(f"Route decision: {receipt.route_decision_id}")
    print(f"Outcome: {receipt.outcome.value}")
    print(f"Recommended route: {receipt.recommended_route_id or 'none'}")
    for item in receipt.eligible_routes:
        selected = " selected" if item.route_id == receipt.recommended_route_id else ""
        print(
            f"eligible  {item.route_id} agent={item.agent_id} "
            f"cost={item.cost.knowledge.value}{selected}"
        )
    for item in receipt.rejected_routes:
        print(f"rejected  {item.route_id} reasons={','.join(item.reason_codes)}")
    print("Execution started: no")


def _sorted_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("route command clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "RouteCommandHandlers",
    "RouteCommandService",
    "build_local_route_source",
    "route_query_from_args",
]
