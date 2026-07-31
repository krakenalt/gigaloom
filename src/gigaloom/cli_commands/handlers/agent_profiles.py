"""CLI handlers for declarative Agent Profile registration and probe planning."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import shutil
import sys
from typing import Any

from gigaloom.cli_commands.parser import build_parser
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.agent_profiles import (
    AgentProbePlanStatus,
    AgentProfileV1,
    AgentProfileRegistrySnapshot,
    CoreCommandCollisionContractV1,
    build_core_command_collision_contract,
    discover_installed_agent_profile_candidates,
    load_agent_profile_registry,
    plan_agent_probe,
    register_local_agent_profile,
    remove_registered_agent_profile,
)


def _handle_agent_profile_list(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    snapshot = _snapshot(config)
    payload = {
        "schema_version": 1,
        "state_revision": snapshot.state_revision,
        "agents": [_profile_payload(item) for item in snapshot.registry.profiles],
        "issues": [asdict(item) for item in snapshot.issues],
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"{'ID':<20}{'Source':<18}{'Native':<12}Name")
        for profile in snapshot.registry.profiles:
            native_status = _native_status(profile)
            print(
                f"{profile.agent_id:<20}{profile.source.kind.value:<18}"
                f"{native_status:<12}{profile.display_name}"
            )
        for issue in snapshot.issues:
            print(
                f"{issue.agent_id}: {issue.code}: {issue.message}",
                file=sys.stderr,
            )
    return 0 if not snapshot.issues else 1


def _handle_agent_profile_inspect(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    snapshot = _snapshot(config)
    profile = _get_profile(snapshot, args.agent_id)
    payload = _profile_payload(profile)
    payload["state_revision"] = snapshot.state_revision
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _handle_agent_profile_add(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    result = register_local_agent_profile(
        config.data_dir,
        args.manifest,
        collision_contract=_collision_contract(),
        dry_run=args.dry_run,
    )
    payload = asdict(result)
    if args.json:
        _print_json(payload)
    else:
        verb = "Would register" if result.dry_run else "Registered"
        suffix = " (unchanged)" if not result.changed else ""
        print(f"{verb} agent {result.agent_id}{suffix}")
    return 0


def _handle_agent_profile_remove(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    result = remove_registered_agent_profile(
        config.data_dir,
        args.agent_id,
        dry_run=args.dry_run,
    )
    payload = asdict(result)
    payload["provider_artifacts_removed"] = False
    if args.json:
        _print_json(payload)
    else:
        verb = (
            "Would remove registration for"
            if result.dry_run
            else "Removed registration for"
        )
        print(f"{verb} agent {result.agent_id}; provider artifacts were not removed")
    return 0


def _handle_agent_probe_plan(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    snapshot = _snapshot(config)
    profile = _get_profile(snapshot, args.agent_id)
    plan = plan_agent_probe(
        profile,
        route_id=args.route,
        environment=os.environ,
        facade_executable=sys.argv[0],
        platform=sys.platform,
    )
    payload = asdict(plan)
    payload["status"] = plan.status.value
    if args.json:
        _print_json(payload)
    else:
        route = f" route={plan.route_id}" if plan.route_id is not None else ""
        print(
            f"Agent probe plan: agent={plan.agent_id}{route} "
            f"owner={plan.owner} status={plan.status.value}"
        )
        print("Execution performed: no")
    return 0 if plan.status is AgentProbePlanStatus.PLANNED else 1


def _handle_agent_profile_discover(
    args: argparse.Namespace,
    _config: HarnessConfig,
) -> int:
    if args.registry != "installed":
        raise ValueError(
            "agent discovery registry is unsupported; available registry: installed"
        )
    candidates = discover_installed_agent_profile_candidates()
    payload = {
        "registry": "installed",
        "dry_run": args.dry_run,
        "execution_performed": False,
        "installation_performed": False,
        "candidates": [asdict(item) for item in candidates],
    }
    if args.json:
        _print_json(payload)
    else:
        for candidate in candidates:
            print(
                f"{candidate.entry_point_name:<20}"
                f"{candidate.distribution_name}=={candidate.distribution_version}"
            )
        if not candidates:
            print("No installed Agent Profile entry points discovered")
    return 0


def _snapshot(config: HarnessConfig) -> AgentProfileRegistrySnapshot:
    return load_agent_profile_registry(
        config.data_dir,
        collision_contract=_collision_contract(),
    )


def _collision_contract() -> CoreCommandCollisionContractV1:
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    choices = command_action.choices
    if choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    return build_core_command_collision_contract(choices)


def _get_profile(
    snapshot: AgentProfileRegistrySnapshot,
    agent_id: str,
) -> AgentProfileV1:
    try:
        return snapshot.registry.get(agent_id)
    except KeyError as exc:
        issue = next(
            (item for item in snapshot.issues if item.agent_id == agent_id),
            None,
        )
        if issue is not None:
            raise ValueError(
                f"agent profile is stale: {agent_id}: {issue.code}"
            ) from exc
        raise ValueError(f"unknown agent profile: {agent_id}") from exc


def _profile_payload(profile) -> dict[str, Any]:
    native = profile.native
    return {
        "schema_version": profile.schema_version,
        "agent_id": profile.agent_id,
        "display_name": profile.display_name,
        "aliases": list(profile.aliases),
        "profile_version": profile.profile_version,
        "profile_digest": profile.profile_digest,
        "source": {
            "kind": profile.source.kind.value,
            "origin": profile.source.origin,
            "revision": profile.source.revision,
            "trust_class": profile.source.trust_class.value,
            "reviewed": profile.source.reviewed,
        },
        "platform_support": list(profile.platform_support),
        "native": None
        if native is None
        else {
            "executable_names": list(native.executable_names),
            "readiness": _native_status(profile),
            "version_probe_available": native.version_probe is not None,
            "supports_managed_terminal": native.supports_managed_terminal,
        },
        "structured_route_ids": [route.route_id for route in profile.structured_routes],
    }


def _native_status(profile) -> str:
    native = profile.native
    if native is None:
        return "unavailable"
    return (
        "ready"
        if any(shutil.which(name) is not None for name in native.executable_names)
        else "missing"
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
