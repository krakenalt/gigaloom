"""Private atomic doctor support-report export."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .models import _sanitize_report


def write_doctor_support_report(
    report: Mapping[str, Any],
    output: str | Path,
) -> Path:
    """Atomically write one canonical private JSON report for support."""
    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    payload = json.dumps(
        _sanitize_report(dict(report)),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"{payload}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination
