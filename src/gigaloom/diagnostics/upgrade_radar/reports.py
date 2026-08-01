"""Byte-stable immutable storage for recommendation-only upgrade reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, cast

from gigaloom.contracts import (
    UpgradeRadarReportV1,
    upgrade_radar_report_from_dict,
    upgrade_radar_report_to_dict,
)


MAX_UPGRADE_REPORT_BYTES = 1024 * 1024


def upgrade_report_bytes(report: UpgradeRadarReportV1) -> bytes:
    """Encode canonical replay-stable JSON with no terminal formatting."""
    if not isinstance(report, UpgradeRadarReportV1):
        raise ValueError("upgrade report is invalid")
    return (
        json.dumps(
            upgrade_radar_report_to_dict(report),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("ascii")


def upgrade_report_from_bytes(payload: bytes) -> UpgradeRadarReportV1:
    """Decode bounded canonical report JSON through the frozen strict codec."""
    if (
        not isinstance(payload, bytes)
        or not payload
        or (len(payload) > MAX_UPGRADE_REPORT_BYTES)
    ):
        raise ValueError("upgrade report bytes exceed the bounded policy")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("upgrade report is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("upgrade report must be a JSON object")
    report = upgrade_radar_report_from_dict(cast(Mapping[str, Any], decoded))
    if upgrade_report_bytes(report) != payload:
        raise ValueError("upgrade report bytes are not canonical")
    return report


def save_upgrade_report(path: str | Path, report: UpgradeRadarReportV1) -> None:
    """Persist once with private permissions; identical retries are idempotent."""
    target = Path(path)
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("upgrade report path cannot be a symlink")
    payload = upgrade_report_bytes(report)
    if target.exists():
        if not target.is_file() or _read_bounded(target) != payload:
            raise ValueError("immutable upgrade report already exists")
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not target.is_file() or _read_bounded(target) != payload:
                raise ValueError("immutable upgrade report already exists") from None
    finally:
        temporary.unlink(missing_ok=True)


def load_upgrade_report(path: str | Path) -> UpgradeRadarReportV1:
    """Load one regular immutable report and verify canonical replay bytes."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("upgrade report path must be a regular file")
    return upgrade_report_from_bytes(_read_bounded(target))


def _read_bounded(path: Path) -> bytes:
    size = path.stat().st_size
    if size < 1 or size > MAX_UPGRADE_REPORT_BYTES:
        raise ValueError("upgrade report bytes exceed the bounded policy")
    return path.read_bytes()


__all__ = [
    "MAX_UPGRADE_REPORT_BYTES",
    "load_upgrade_report",
    "save_upgrade_report",
    "upgrade_report_bytes",
    "upgrade_report_from_bytes",
]
