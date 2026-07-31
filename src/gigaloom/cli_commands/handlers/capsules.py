"""CLI handlers for Run Capsule export and strictly offline verification."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from typing import Any

from gigaloom.config import HarnessConfig
from gigaloom.review.api import (
    FilesystemRunCapsuleRepository,
    verify_run_capsule,
)


def _handle_capsule_export(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository = FilesystemRunCapsuleRepository(config.data_dir)
    destination = repository.export_run(args.run_id, args.output)
    record = repository.get_by_run(args.run_id)
    payload = {
        **record.to_dict(),
        "output_path": str(destination.resolve()),
    }
    _emit(payload, json_output=args.json)
    return 0


def _handle_capsule_verify(
    args: argparse.Namespace,
    _config: HarnessConfig,
) -> int:
    report = verify_run_capsule(args.path, checkout=args.checkout)
    payload = {
        "schema_version": 1,
        "kind": "gigaloom.run_capsule.verification_report.v1",
        "capsule_id": report.capsule_id,
        "capsule_sha256": report.capsule_sha256,
        "status": report.status.value,
        "verified": report.verified,
        "signature": {
            "status": report.signature_status.value,
            "valid": report.signature_valid,
            "signer_id": report.signer_id,
            "trust_status": report.trust_status,
        },
        "findings": [
            {
                **asdict(finding),
                "status": finding.status.value,
            }
            for finding in report.findings
        ],
        "correctness_claimed": False,
        "network_accessed": False,
    }
    _emit(payload, json_output=args.json)
    return 0 if report.verified else 1


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload["kind"] == "gigaloom.run_capsule.verification_report.v1":
        print(
            f"Capsule {payload['capsule_id']}: {payload['status']} "
            f"(signature: {payload['signature']['status']})"
        )
        for finding in payload["findings"]:
            print(f"  {finding['field']}: {finding['status']}")
        print("This verifies integrity and requested facts, not correctness.")
        return
    print(f"Exported capsule {payload['capsule_id']} to {payload['output_path']}")


__all__ = ["_handle_capsule_export", "_handle_capsule_verify"]
