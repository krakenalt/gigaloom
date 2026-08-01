"""CLI projection for bounded state validation and hermetic fault fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from gigaloom.config import HarnessConfig
from gigaloom.diagnostics.api import (
    FaultLabRunner,
    FaultScenarioStatus,
    RecoveryCheckService,
    fault_scenario_result_to_dict,
    recovery_scan_report_to_dict,
)


def _handle_reliability_check(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Run one read-only bounded scan against the selected data root."""
    root = args.data_dir if args.data_dir is not None else config.data_dir
    payload = recovery_scan_report_to_dict(RecoveryCheckService().check(root))
    _write_projection(payload, as_json=bool(args.json))
    return 1 if payload["status"] == "failed" else 0


def _handle_reliability_simulate(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Run one fixture only inside a disposable admitted sandbox."""
    result = FaultLabRunner(active_data_root=config.data_dir).run(
        args.fixture,
        sandbox_parent=Path(args.sandbox),
    )
    payload = fault_scenario_result_to_dict(result)
    _write_projection(payload, as_json=bool(args.json))
    return 0 if result.status is FaultScenarioStatus.PASSED else 1


def _write_projection(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return
    if payload["kind"] == "gigaloom_reliability_check":
        summary = payload["summary"]
        bounds = payload["bounds"]
        sys.stdout.write(
            f"Reliability check: {payload['status']}\n"
            f"Checks: {bounds['checks_observed']} "
            f"({summary['failed']} failed, {summary['warning']} warning)\n"
            f"Files observed: {bounds['files_observed']}\n"
            f"Data root fingerprint: {payload['data_root_fingerprint']}\n"
        )
        return
    sys.stdout.write(
        f"Reliability fixture: {payload['fixture_id']}\n"
        f"Status: {payload['status']}\n"
        f"Invariants: {len(payload['invariants'])}\n"
        f"Result digest: {payload['result_digest']}\n"
    )


__all__ = [
    "_handle_reliability_check",
    "_handle_reliability_simulate",
]
