"""CLI projections for the shared managed ACP agent lifecycle service."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import platform as platform_module
import sys
from typing import Any, Callable

from gigaloom.config import HarnessConfig
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_to_dict,
    agent_install_plan_to_dict,
)
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentRuntimeService,
    InstallPlanningResult,
    discover_local_install_coordinator,
)
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAcpProbeRunner,
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import (
    managed_probe_to_dict,
)
from gigaloom.harnesses.agent_profiles.registry import (
    ACPRegistryCache,
    OfficialACPRegistryClient,
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
    agents = [asdict(item) for item in _service(config).list()]
    _emit({"schema_version": 1, "agents": agents}, as_json=args.json)
    return 0


def _handle_agent_runtime_inspect(
    args: argparse.Namespace, config: HarnessConfig
) -> int:
    result = _service(config).inspect(args.local_agent_id)
    _emit(_result_payload(result), as_json=args.json)
    return 0


def _handle_agent_runtime_probe(args: argparse.Namespace, config: HarnessConfig) -> int:
    result = _service(config).probe(args.local_agent_id)
    _emit(managed_probe_to_dict(result), as_json=args.json)
    return 0


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
    removed = _service(config).remove(
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
    return build_agent_runtime_service(
        config,
        network_isolation_admitted=False,
    )


def build_agent_runtime_service(
    config: HarnessConfig,
    *,
    network_isolation_admitted: bool,
    platform_id: str | None = None,
    architecture: str | None = None,
    reserved_inventory: AgentIdentityInventory = AgentIdentityInventory(),
) -> AgentRuntimeService:
    """Compose the shared service with explicit host and isolation authority."""

    def clock() -> datetime:
        return datetime.now(UTC)

    host_platform = platform_id or (
        "windows" if sys.platform == "win32" else sys.platform
    )
    host_architecture = architecture or _host_architecture()
    cache = ACPRegistryCache(Path(config.data_dir) / "agent_profiles/acp_registry")
    return AgentRuntimeService(
        config.data_dir,
        OfficialACPRegistryClient(cache=cache),
        discover_local_install_coordinator(
            config.data_dir,
            platform=host_platform,
            architecture=host_architecture,
            probe=ManagedAcpProbeRunner(),
            network_isolation_admitted=network_isolation_admitted,
            clock=clock,
        ),
        clock=clock,
        reserved_inventory=reserved_inventory,
    )


def _host_architecture() -> str:
    machine = platform_module.machine().lower()
    aliases = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "x64": "x86_64",
    }
    return aliases.get(machine, machine)


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
