"""Capture comparable p50/p95 latency for the attachment UTF-8 fast path."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import timeit
from typing import Any

from gigaloom.attachments import decode_attachment_text

WORKLOAD_REPETITIONS = 12_000
WARMUP_CALLS = 50
BATCHES = 40
CALLS_PER_BATCH = 20


def capture() -> dict[str, Any]:
    """Return one machine-readable benchmark sample set summary."""
    payload = ("# hello мир\nprint(42)\n" * WORKLOAD_REPETITIONS).encode()
    timer = timeit.Timer(lambda: decode_attachment_text(payload))
    timer.timeit(number=WARMUP_CALLS)
    samples = sorted(
        value / CALLS_PER_BATCH * 1_000
        for value in timer.repeat(repeat=BATCHES, number=CALLS_PER_BATCH)
    )
    repository_root = Path(__file__).resolve().parents[3]
    return {
        "schema_version": "gigaloom.attachment-utf8-benchmark.v1",
        "source_revision": _git(repository_root, "rev-parse", "HEAD"),
        "source_dirty": bool(_git(repository_root, "status", "--short")),
        "lock_sha256": hashlib.sha256(
            (repository_root / "uv.lock").read_bytes()
        ).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "workload": {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "warmup_calls": WARMUP_CALLS,
            "batches": BATCHES,
            "calls_per_batch": CALLS_PER_BATCH,
        },
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
    }


def main() -> int:
    """Print or persist one deterministic-workload benchmark capture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"label": args.label, **capture()}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


def _percentile(values: list[float], percentile: float) -> float:
    index = max(math.ceil(len(values) * percentile) - 1, 0)
    return values[index]


def _git(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
