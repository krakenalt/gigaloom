"""Environment and process evidence for runtime performance profiles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from typing import Any

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None


def resource_snapshot() -> tuple[int, int]:
    """Return peak RSS and cumulative voluntary/involuntary wakeups."""
    if resource is None:
        return 0, 0
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    return rss, int(usage.ru_nvcsw + usage.ru_nivcsw)


def environment() -> dict[str, Any]:
    """Return public environment fields with a deterministic fingerprint."""
    fields = {
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    payload = json.dumps(fields, separators=(",", ":"), sort_keys=True).encode()
    return {
        **fields,
        "fingerprint": {
            "algorithm": "sha256",
            "value": hashlib.sha256(payload).hexdigest(),
            "fields": sorted(fields),
        },
    }


def source_commit() -> str:
    """Resolve the source revision without retaining a checkout path."""
    for name in ("GITHUB_SHA", "CI_COMMIT_SHA"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parent,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"
