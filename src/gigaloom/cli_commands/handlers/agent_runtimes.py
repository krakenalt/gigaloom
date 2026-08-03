"""CLI projections for the shared managed ACP agent lifecycle service."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Callable

from gigaloom.config import HarnessConfig
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_to_dict,
    agent_install_plan_to_dict,
)
from gigaloom.harnesses.agent_profiles import (
    AgentProfileSourceKind,
    build_core_command_collision_contract,
    load_agent_profile_registry,
)
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentRuntimeService,
    InstallPlanningResult,
    create_agent_runtime_service,
)
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import (
    managed_probe_to_dict,
)


AgentRuntimeServiceFactory = Callable[[HarnessConfig], AgentRuntimeService]
_SERVICE_FACTORY: AgentRuntimeServiceFactory | None = None


def configure_agent_runtime_service_factory(
    factory: AgentRuntimeServiceFactory | None,
) -> None:
    """Install the composition-owned service factory without global side effects."""
    global _SERVICE_FACTORY
    _SERVICE_FACTORY = factory


def _handle_agent_runtime_search(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    page = _service(config).search(args.query, refresh=args.refresh)
    payload = {
        "schema_version": 1,
        "snapshot_digest": page.catalog.snapshot.snapshot_digest,
        "stale": page.catalog.snapshot.stale,
        "offline": page.catalog.offline,
        "entries": [_entry_payload(item) for item in page.entries],
    }
    _emit(payload, as_json=args.json)
    return 0


def _handle_agent_runtime_add(args: argparse.Namespace, config: HarnessConfig) -> int:
    if args.manifest is not None:
        if args.registry_query is not None:
            raise ValueError("agent add accepts either a registry query or --manifest")
        from gigaloom.cli_commands.handlers.agent_profiles import (
            _handle_agent_profile_add,
        )

        return _handle_agent_profile_add(args, config)
    if args.registry_query is None:
        raise ValueError("agent add requires a registry query or --manifest")
    result = _service(config).add(
        args.registry_query,
        local_agent_id=args.local_agent_id,
        dry_run=args.dry_run,
        confirmed=(False if args.dry_run else _confirmed(args, "Install agent?")),
        allow_unverified=args.allow_unverified,
        refresh=args.refresh,
    )
    payload = (
        _planning_payload(result)
        if isinstance(result, InstallPlanningResult)
        else _result_payload(result)
    )
    _emit(payload, as_json=args.json)
    return (
        0
        if not isinstance(result, InstallPlanningResult) or result.plan is not None
        else 2
    )


def _handle_agent_runtime_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    runtime = _service(config)
    installed = runtime.list()
    profiles, _ = _profile_inventory(config)
    combined = {item.agent_id: item for item in profiles}
    managed = {item.agent_id: item for item in runtime.active_profiles()}
    if combined.keys() & managed.keys():
        raise ValueError("managed agent identity collides with a registered profile")
    combined.update(managed)
    from gigaloom.cli_commands.handlers.agent_profiles import _profile_payload

    _emit(
        {
            "schema_version": 1,
            "agents": [_profile_payload(combined[item]) for item in sorted(combined)],
            "installed_revisions": [asdict(item) for item in installed],
        },
        as_json=args.json,
    )
    return 0


def _handle_agent_runtime_inspect(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    runtime = _service(config)
    if args.local_agent_id not in _managed_agent_ids(runtime):
        from gigaloom.cli_commands.handlers.agent_profiles import (
            _handle_agent_profile_inspect,
        )

        return _handle_agent_profile_inspect(_profile_args(args), config)
    result = runtime.inspect(args.local_agent_id)
    _emit(_result_payload(result), as_json=args.json)
    return 0


def _handle_agent_runtime_probe(args: argparse.Namespace, config: HarnessConfig) -> int:
    runtime = _service(config)
    if args.local_agent_id not in _managed_agent_ids(runtime):
        from gigaloom.cli_commands.handlers.agent_profiles import (
            _handle_agent_probe_plan,
        )

        return _handle_agent_probe_plan(_profile_args(args), config)
    result = runtime.probe(args.local_agent_id)
    _emit(managed_probe_to_dict(result), as_json=args.json)
    return 0


def _handle_agent_runtime_activate(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    result = _service(config).activate(
        args.local_agent_id,
        install_id=args.install_id,
        confirmed=_confirmed(args, "Re-probe and activate managed agent revision?"),
    )
    _emit(_result_payload(result), as_json=args.json)
    return 0 if result.active else 2


def _handle_agent_runtime_outdated(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    agents = [asdict(item) for item in _service(config).outdated(refresh=args.refresh)]
    _emit({"schema_version": 1, "agents": agents}, as_json=args.json)
    return 0


def _handle_agent_runtime_update(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    result = _service(config).update(
        args.local_agent_id,
        confirmed=_confirmed(args, "Update agent side-by-side?"),
        allow_unverified=args.allow_unverified,
        refresh=args.refresh,
    )
    _emit(_result_payload(result), as_json=args.json)
    return 0


def _handle_agent_runtime_rollback(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    if not _confirmed(args, "Roll back active agent?"):
        raise ValueError("managed agent rollback requires confirmation")
    result = _service(config).rollback(args.local_agent_id)
    _emit(agent_activation_to_dict(result), as_json=args.json)
    return 0


def _handle_agent_runtime_remove(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    runtime = _service(config)
    if args.local_agent_id not in _managed_agent_ids(runtime):
        from gigaloom.cli_commands.handlers.agent_profiles import (
            _handle_agent_profile_remove,
        )

        return _handle_agent_profile_remove(_profile_args(args), config)
    if args.dry_run:
        count = sum(
            item.local_agent_id == args.local_agent_id for item in runtime.list()
        )
        _emit(
            {
                "schema_version": 1,
                "local_agent_id": args.local_agent_id,
                "dry_run": True,
                "removed_install_count": 0,
                "would_remove_install_count": count,
            },
            as_json=args.json,
        )
        return 0
    removed = runtime.remove(
        args.local_agent_id,
        confirmed=_confirmed(args, "Remove managed agent artifacts?"),
    )
    _emit(
        {
            "schema_version": 1,
            "local_agent_id": args.local_agent_id,
            "removed_install_count": removed,
            "native_or_provider_artifacts_removed": False,
        },
        as_json=args.json,
    )
    return 0


def _handle_agent_runtime_lock(args: argparse.Namespace, config: HarnessConfig) -> int:
    value = _service(config).lock(args.output)
    _emit(
        {
            "schema_version": 1,
            "output": str(Path(args.output)),
            "lockset_digest": value.lockset_digest,
            "agent_count": len(value.agents),
        },
        as_json=args.json,
    )
    return 0


def _handle_agent_runtime_sync(args: argparse.Namespace, config: HarnessConfig) -> int:
    results = _service(config).sync(
        args.lock,
        confirmed=_confirmed(args, "Install exact locked agents?"),
    )
    _emit(
        {
            "schema_version": 1,
            "installed": [_result_payload(item) for item in results],
            "upgraded": False,
        },
        as_json=args.json,
    )
    return 0


def _service(config: HarnessConfig) -> AgentRuntimeService:
    if _SERVICE_FACTORY is not None:
        return _SERVICE_FACTORY(config)
    return build_agent_runtime_service(config)


def build_agent_runtime_service(
    config: HarnessConfig,
    *,
    network_isolation_admitted: bool | None = None,
    platform_id: str | None = None,
    architecture: str | None = None,
    reserved_inventory: AgentIdentityInventory | None = None,
) -> AgentRuntimeService:
    """Compose the shared service with explicit host and isolation authority."""
    if reserved_inventory is None:
        _, reserved_inventory = _profile_inventory(config)
    return create_agent_runtime_service(
        config.data_dir,
        network_isolation_admitted=network_isolation_admitted,
        platform_id=platform_id,
        architecture=architecture,
        reserved_inventory=reserved_inventory,
    )


def _profile_inventory(
    config: HarnessConfig,
) -> tuple[tuple[Any, ...], AgentIdentityInventory]:
    from gigaloom.cli_commands.parser import build_parser

    parser = build_parser()
    action = next(item for item in parser._actions if item.dest == "command")
    if action.choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    commands = tuple(sorted(action.choices))
    profiles = load_agent_profile_registry(
        config.data_dir,
        collision_contract=build_core_command_collision_contract(commands),
    ).registry.profiles
    return profiles, AgentIdentityInventory(
        core_commands=commands,
        native_agent_ids=tuple(
            item.agent_id for item in profiles if item.native is not None
        ),
        native_aliases=tuple(
            alias
            for item in profiles
            if item.native is not None
            for alias in item.aliases
        ),
        local_agent_ids=tuple(
            item.agent_id
            for item in profiles
            if item.source.kind is AgentProfileSourceKind.LOCAL_MANIFEST
        ),
    )


def _managed_agent_ids(runtime: AgentRuntimeService) -> frozenset[str]:
    return frozenset(item.local_agent_id for item in runtime.list())


def _profile_args(args: argparse.Namespace) -> argparse.Namespace:
    values = vars(args).copy()
    values["agent_id"] = args.local_agent_id
    values.setdefault("route", None)
    values.setdefault("dry_run", False)
    return argparse.Namespace(**values)


def _entry_payload(entry) -> dict[str, Any]:  # noqa: ANN001
    return {
        "registry_id": entry.registry_id,
        "name": entry.name,
        "version": entry.version,
        "description": entry.description,
        "license": entry.license,
        "entry_digest": entry.entry_digest,
        "snapshot_digest": entry.snapshot_digest,
        "distributions": [
            {
                "kind": item.kind.value,
                "platform": item.platform,
                "architecture": item.architecture,
                "integrity": (
                    "verified" if item.expected_integrity is not None else "unverified"
                ),
                "distribution_digest": item.distribution_digest,
            }
            for item in entry.distributions
        ],
    }


def _planning_payload(result: InstallPlanningResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reason_code": result.reason_code,
        "local_agent_id": result.local_agent_id,
        "proposed_local_agent_id": result.proposed_local_agent_id,
        "collision_namespaces": list(result.collision_namespaces),
        "plan": (
            agent_install_plan_to_dict(result.plan) if result.plan is not None else None
        ),
        "decisions": [
            {
                "distribution_digest": item.distribution_digest,
                "rank": item.rank,
                "status": item.status.value,
                "reason_code": item.reason_code,
            }
            for item in result.decisions
        ],
    }


def _result_payload(result: ManagedAgentOnboardingResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "local_agent_id": result.artifact.local_agent_id,
        "registry_id": result.artifact.registry_id,
        "version": result.artifact.version,
        "install_id": result.artifact.install_id,
        "artifact_digest": result.artifact.artifact_digest,
        "profile_digest": result.profile.profile_digest,
        "probe_state": result.probe.state.value,
        "compatibility_status": result.compatibility.status.value,
        "activation_status": result.activation.status.value,
        "active": result.active,
        "receipt_id": result.receipt.receipt_id,
        "omissions": list(result.receipt.omissions),
    }


def _confirmed(args: argparse.Namespace, prompt: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        raise ValueError("interactive confirmation unavailable; pass --yes")
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
