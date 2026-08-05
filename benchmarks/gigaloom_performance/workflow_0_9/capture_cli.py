"""Capture isolated GigaLoom CLI startup p50/p95 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import tempfile
from time import perf_counter
from typing import Any


SCHEMA_VERSION = "gigaloom.cli-startup-performance.v1"
COMMANDS = (
    ("version", ("--version",)),
    ("root_help", ("--help",)),
    ("agent_list_json", ("agent", "list", "--json")),
    ("builtin_agent_help", ("codex", "--help")),
)


def _percentile(ordered: list[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": round(_percentile(ordered, 0.50), 6),
        "p95": round(_percentile(ordered, 0.95), 6),
    }


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


def _run(
    executable: Path,
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        (os.fspath(executable), *arguments),
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        timeout=10,
    )


def capture(
    repository_root: Path,
    *,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    """Measure the release CLI set without reading user-owned state."""
    if not 5 <= samples <= 50:
        raise ValueError("samples must be between 5 and 50")
    if not 1 <= warmups <= 10:
        raise ValueError("warmups must be between 1 and 10")
    executable = repository_root / ".venv" / "bin" / "giga"
    if not executable.is_file():
        raise ValueError("run ./scripts/ci-base.sh sync first")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="gigaloom-091-cli-") as temporary:
        isolated = Path(temporary)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": os.fspath(isolated / "home"),
                "CODEX_HOME": os.fspath(isolated / "codex"),
                "GIGALOOM_DATA_DIR": os.fspath(isolated / "gigaloom"),
                "XDG_CACHE_HOME": os.fspath(isolated / "cache"),
                "XDG_CONFIG_HOME": os.fspath(isolated / "config"),
            }
        )
        for case_id, arguments in COMMANDS:
            for _ in range(warmups):
                _run(
                    executable,
                    arguments,
                    repository_root=repository_root,
                    environment=environment,
                )
            elapsed: list[float] = []
            output_bytes: list[float] = []
            for _ in range(samples):
                started = perf_counter()
                completed = _run(
                    executable,
                    arguments,
                    repository_root=repository_root,
                    environment=environment,
                )
                elapsed.append((perf_counter() - started) * 1_000)
                output_bytes.append(
                    float(len(completed.stdout) + len(completed.stderr))
                )
            results.append(
                {
                    "id": f"cli.{case_id}",
                    "fixture": {
                        "argv": ["giga", *arguments],
                        "isolated_home": True,
                        "external_network_accessed": False,
                    },
                    "latency_ms": _summary(elapsed),
                    "counters": {"output_bytes": _summary(output_bytes)},
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_revision": _git(repository_root, "rev-parse", "HEAD"),
        "source_dirty": bool(_git(repository_root, "status", "--short")),
        "lock_sha256": hashlib.sha256(
            (repository_root / "uv.lock").read_bytes()
        ).hexdigest(),
        "environment": {
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "warmups_per_command": warmups,
        "samples_per_command": samples,
        "privacy": {
            "content_free": True,
            "external_network_accessed": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "temporary_state_only": True,
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", default=20, type=int)
    parser.add_argument("--warmups", default=3, type=int)
    args = parser.parse_args()
    repository_root = Path.cwd().resolve()
    result = {
        "label": args.label,
        **capture(
            repository_root,
            samples=args.samples,
            warmups=args.warmups,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
