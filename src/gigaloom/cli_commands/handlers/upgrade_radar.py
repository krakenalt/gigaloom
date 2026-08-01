"""CLI projection for explicit recommendation-only agent upgrade checks."""

from __future__ import annotations

import argparse
import json
import sys

from gigaloom.config import HarnessConfig
from gigaloom.diagnostics.api import (
    UpgradeRadarService,
    upgrade_radar_check_to_dict,
)


def _handle_agent_upgrade_check(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Compare an installed route with one explicit, never-installed candidate."""
    result = UpgradeRadarService(config.data_dir).check(
        args.agent_id,
        candidate_command=args.candidate_command,
        corpus_id=args.corpus,
    )
    payload = upgrade_radar_check_to_dict(result)
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    report = result.report
    sys.stdout.write(
        f"Upgrade radar: {result.agent_id}\n"
        f"Recommendation: {report.recommendation.value}\n"
        f"Current revision: {report.current_route.revision_digest[:12]}\n"
        f"Candidate revision: {report.candidate_route.revision_digest[:12]}\n"
        f"Report: {report.report_id}\n"
        "No install, update, or automatic apply was authorized.\n"
    )
    return 0


__all__ = ["_handle_agent_upgrade_check"]
