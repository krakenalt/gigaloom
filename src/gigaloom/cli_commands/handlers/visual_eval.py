"""CLI projection for the reusable local Visual QA Eval gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from gigaloom.automation.api import run_visual_eval
from gigaloom.config import HarnessConfig
from gigaloom.contracts import (
    operational_evidence_to_dict,
    visual_gate_receipt_to_dict,
)


def _handle_eval_visual(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run the exact local target through the reusable Visual QA Eval gate."""
    result = run_visual_eval(
        target_url=args.url,
        assertions=tuple(args.assertions),
        evidence_root=Path(config.data_dir) / "automation" / "visual-qa-v1",
    )
    payload = {
        "assertions": [item.value for item in result.requested_assertions],
        "eval_evidence": operational_evidence_to_dict(result.eval_evidence),
        "visual_gate": visual_gate_receipt_to_dict(result.receipt),
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        receipt = result.receipt
        sys.stdout.write(
            f"Visual QA: {receipt.status.value}\n"
            f"Gate: {receipt.gate_id}\n"
            f"Origin: {receipt.origin}\n"
            f"Console errors: {receipt.console_summary.failure_count}\n"
            f"Request failures: {receipt.request_summary.failure_count}\n"
            "Viewports: desktop, mobile (390x844)\n"
        )
    return 0 if result.receipt.status.value == "passed" else 1


__all__ = ["_handle_eval_visual"]
