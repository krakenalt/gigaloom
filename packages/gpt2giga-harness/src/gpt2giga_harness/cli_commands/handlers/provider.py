"""Provider CLI handlers."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Mapping

from gpt2giga_harness.cli_commands.output import print_json
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.provider_migration import ProviderMigrationService
from gpt2giga_harness.provider_settings import ProviderSettingsService


def _handle_provider_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = ProviderSettingsService(config.data_dir).list()
    if args.json:
        print_json(payload)
    else:
        _print_provider_table(payload["providers"])
    return 0


def _handle_provider_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    provider = ProviderSettingsService(config.data_dir).get(args.provider_id)
    if args.json:
        print_json(provider)
    else:
        _print_provider_detail(provider)
    return 0


def _handle_provider_add(args: argparse.Namespace, config: HarnessConfig) -> int:
    service = ProviderSettingsService(config.data_dir)
    result = service.create(
        args.provider_id,
        _provider_payload_from_args(args, create=True),
    )
    if args.json:
        print_json(
            {"saved": True, "provider": result.provider, "effects": result.effects}
        )
    else:
        _print_provider_detail(result.provider)
    return 0


def _handle_provider_edit(args: argparse.Namespace, config: HarnessConfig) -> int:
    service = ProviderSettingsService(config.data_dir)
    result = service.update(
        args.provider_id,
        _provider_payload_from_args(args, create=False),
        expected_revision=args.expected_revision,
    )
    if args.json:
        print_json(
            {"saved": True, "provider": result.provider, "effects": result.effects}
        )
    else:
        _print_provider_detail(result.provider)
    return 0


def _handle_provider_test(args: argparse.Namespace, config: HarnessConfig) -> int:
    return _handle_provider_probe(args, config, discover_models=False)


def _handle_provider_discover(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    return _handle_provider_probe(args, config, discover_models=True)


def _handle_provider_probe(
    args: argparse.Namespace,
    config: HarnessConfig,
    *,
    discover_models: bool,
) -> int:
    payload = ProviderSettingsService(config.data_dir).check(
        args.provider_id,
        discover_models=discover_models,
    )
    if args.json:
        print_json(payload)
    else:
        health = payload["health"]
        print(f"Provider: {payload['provider_id']}")
        print(f"Health: {health['status']}")
        print(f"Discovery: {health['discovery_status']}")
        if health["failure_kind"]:
            print(
                f"Failure: {health['failure_kind']} ({health['reason_code']})",
                file=sys.stderr,
            )
        for model in health["models"]:
            print(f"- {model['model']} [{model['source']}]")
    return 0 if payload["health"]["status"] == "ready" else 1


def _handle_provider_migrate(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    service = ProviderMigrationService(config.data_dir, config)
    if args.dry_run:
        payload = service.plan().to_dict()
    else:
        if args.backup is None:
            raise ValueError("provider migration requires --backup or --dry-run")
        payload = service.migrate(args.backup).to_dict()
    if args.json:
        print_json(payload)
    else:
        print(f"Provider migration: {payload['status']}")
        print(f"Providers: {', '.join(payload['provider_ids'])}")
        print(f"Routes: {payload['route_count']}")
        if payload.get("applied"):
            print(f"Pre-upgrade backup SHA-256: {payload['backup_sha256']}")
        print("Rollback: stop Harness and restore the verified pre-upgrade archive.")
    return 0


def _provider_payload_from_args(
    args: argparse.Namespace,
    *,
    create: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for argument, field in (
        ("name", "display_name"),
        ("protocol", "protocol"),
        ("dialect", "dialect"),
        ("base_url", "base_url"),
        ("route_prefix", "route_prefix"),
    ):
        value = getattr(args, argument, None)
        if value is not None:
            payload[field] = value
    authentication = getattr(args, "authentication", None)
    auth_values = {
        "ownership": authentication,
        "reference_kind": getattr(args, "secret_reference_kind", None),
        "reference_name": getattr(args, "secret_reference_name", None),
        "service": getattr(args, "keychain_service", None),
        "account": getattr(args, "keychain_account", None),
    }
    if create or any(value is not None for value in auth_values.values()):
        payload["authentication"] = {
            key: value for key, value in auth_values.items() if value is not None
        }
    defaults = {
        purpose: getattr(args, f"{purpose}_model", None)
        for purpose in ("coding", "title", "evaluation", "fallback")
    }
    if any(value is not None for value in defaults.values()):
        payload["default_models"] = {
            purpose: value for purpose, value in defaults.items() if value is not None
        }
    if create:
        payload["enabled"] = not args.disabled
        payload["offline"] = args.offline
    else:
        if args.enabled is not None:
            payload["enabled"] = args.enabled
        if args.offline is not None:
            payload["offline"] = args.offline
    return payload


def _print_provider_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<24}{'Protocol':<24}{'Status':<16}Name")
    for row in rows:
        health = row.get("health") or {}
        status = "disabled" if not row["enabled"] else health.get("status", "unchecked")
        print(
            f"{row['id'][:23]:<24}{row['protocol'][:23]:<24}"
            f"{status[:15]:<16}{row['display_name']}"
        )


def _print_provider_detail(provider: Mapping[str, Any]) -> None:
    print(f"Provider: {provider['display_name']} ({provider['id']})")
    print(f"Protocol: {provider['protocol']} / {provider['dialect']}")
    print(f"Endpoint: {provider['effective_base_url']}")
    print(f"Source: {provider['source']}")
    print(
        "Authentication: "
        f"{provider['authentication']['ownership']} "
        f"({provider['authentication']['reference_kind'] or 'provider-owned'})"
    )
    print(f"Registry revision: {provider['registry_revision']}")
    for purpose, model in provider["default_models"].items():
        print(f"- {purpose}: {model}")
