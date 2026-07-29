"""Harness registry CLI handlers."""

from __future__ import annotations

import argparse
from typing import Any

from gpt2giga_harness.cli_commands.output import print_json
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.plugins import (
    harness_validation_report_to_dict,
    validate_harness_spec,
)
from gpt2giga_harness.registry import create_default_registry
from gpt2giga_harness.types import spec_to_dict
from gpt2giga_harness.workbench_execution import workbench_transport_projection


def _handle_harness_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    del config
    registry = create_default_registry()
    rows = []
    for harness in registry.list():
        spec = harness.spec()
        spec_payload = spec_to_dict(spec)
        availability = harness.availability()
        validation = registry.validation_report(spec.id) or validate_harness_spec(spec)
        rows.append(
            {
                "id": spec_payload["id"],
                "kind": spec_payload["kind"],
                "status": availability.status.value,
                "native": spec_payload["supports_native_sessions"],
                "default_invocation_mode": spec_payload["default_invocation_mode"],
                "workbench_transport": workbench_transport_projection(harness),
                "description": spec_payload["description"],
                "plugin_metadata": spec_payload["plugin_metadata"],
                "validation": harness_validation_report_to_dict(validation),
            }
        )
    if args.json:
        print_json(rows)
    else:
        _print_harness_table(rows)
    return 0


def _print_harness_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<16}{'Kind':<14}{'Status':<12}{'Native':<8}Description")
    for row in rows:
        native = row.get("default_invocation_mode") if row.get("native") else "-"
        print(
            f"{row['id']:<16}{row['kind']:<14}{row['status']:<12}"
            f"{native:<8}{row['description']}"
        )
