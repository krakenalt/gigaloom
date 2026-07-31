"""Bounded private export for content-free performance reports."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any, Final


REPORT_ARTIFACT_MAX_BYTES: Final[dict[str, int]] = {
    "ci-smoke": 64 * 1024,
    "local-detail": 512 * 1024,
    "tui-detail": 2 * 1024 * 1024,
    "runtime-detail": 2 * 1024 * 1024,
}


def write_performance_report(path: str | Path, report: Mapping[str, Any]) -> None:
    """Atomically write a private canonical JSON report."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode()
    policy = report.get("artifact_policy")
    max_bytes = (
        policy.get("max_bytes")
        if isinstance(policy, Mapping)
        else max(REPORT_ARTIFACT_MAX_BYTES.values())
    )
    if not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("performance report artifact limit is invalid")
    if len(payload) > max_bytes:
        raise ValueError(
            f"performance report is {len(payload)} bytes; limit is {max_bytes}"
        )
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
