import hashlib
import json
import math
from pathlib import Path
import timeit

from gigaloom.attachments import decode_attachment_text

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPOSITORY_ROOT / "benchmarks/gigaloom_performance/attachments"


def test_utf8_before_after_evidence_uses_identical_workload_and_environment():
    baseline = _load("baseline.json")
    after = _load("after.json")

    assert baseline["schema_version"] == after["schema_version"]
    assert baseline["workload"] == after["workload"]
    assert baseline["lock_sha256"] == after["lock_sha256"]
    assert baseline["python"] == after["python"]
    assert baseline["platform"] == after["platform"]
    assert baseline["machine"] == after["machine"]
    assert after["p50_ms"] < baseline["p50_ms"]
    assert after["p95_ms"] < baseline["p95_ms"]
    assert after["improvement_pct"]["p50"] == _improvement(
        baseline["p50_ms"], after["p50_ms"]
    )
    assert after["improvement_pct"]["p95"] == _improvement(
        baseline["p95_ms"], after["p95_ms"]
    )


def test_valid_utf8_live_p95_stays_within_reviewed_budget():
    budgets = _load("budgets.json")
    payload = ("# hello мир\nprint(42)\n" * 12_000).encode()
    assert hashlib.sha256(payload).hexdigest() == budgets["workload_sha256"]
    timer = timeit.Timer(lambda: decode_attachment_text(payload))
    timer.timeit(number=10)
    samples = sorted(value / 5 * 1_000 for value in timer.repeat(repeat=12, number=5))
    p95_ms = samples[math.ceil(len(samples) * 0.95) - 1]

    assert p95_ms <= budgets["maximum_live_p95_ms"]


def _load(filename: str):
    return json.loads((EVIDENCE_ROOT / filename).read_text(encoding="utf-8"))


def _improvement(before: float, after: float) -> float:
    return (before - after) / before * 100
