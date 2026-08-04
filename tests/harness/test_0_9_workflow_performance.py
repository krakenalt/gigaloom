"""Release budgets for the GigaLoom 0.9 Work, relay, and gateway workflow."""

from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from benchmarks.gigaloom_performance.workflow_0_9.capture import (
    _enforce_relative_budgets,
    _gateway_cases,
    _relay_cases,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "benchmarks" / "gigaloom_performance" / "workflow_0_9"
EXPECTED_WORKLOADS = {
    "attachments.valid_utf8_decode",
    "evidence.local_export",
    "gateway.preflight",
    "gateway.route_model_discovery",
    "gateway.sidecar_cold_start",
    "gateway.sidecar_warm_attach",
    "instructions.discovery_large",
    "instructions.discovery_small",
    "thread.list_page",
    "thread.read_page",
    "work.initial_load_request_graph",
    "work.run_narrative_update_isolation",
}


def _load(name: str) -> dict[str, object]:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def _results(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["id"]: item for item in report["results"]}


def _p95(item: dict[str, object], counter: str | None = None) -> float:
    summary = item["latency_ms"] if counter is None else item["counters"][counter]
    return float(summary["p95"])


def test_captures_are_comparable_and_report_the_required_workloads() -> None:
    baseline = _load("baseline.json")
    after = _load("after.json")
    before_results = _results(baseline)
    after_results = _results(after)

    assert baseline["schema_version"] == "gigaloom.workflow-0.9-performance.v1"
    assert after["schema_version"] == baseline["schema_version"]
    assert baseline["source_revision"] == ("7143d9bf421765f0207beda7c8abe9a33c4d478b")
    assert baseline["source_dirty"] is False
    assert after["source_revision"] == "6a96d3119021eaa28d56c2bf030eb28f2f35d566"
    assert baseline["lock_sha256"] == after["lock_sha256"]
    assert baseline["environment"] == after["environment"]
    assert baseline["samples_per_workload"] == after["samples_per_workload"] == 20
    assert (
        baseline["privacy"]
        == after["privacy"]
        == {
            "content_free": True,
            "external_network_accessed": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "temporary_state_only": True,
        }
    )
    assert set(before_results) == set(after_results) == EXPECTED_WORKLOADS

    for workload_id, current in after_results.items():
        previous = before_results[workload_id]
        assert current["fixture"] == previous["fixture"]
        for percentile in ("p50", "p95"):
            expected = (
                (previous["latency_ms"][percentile] - current["latency_ms"][percentile])
                / previous["latency_ms"][percentile]
                * 100
            )
            assert current["change_pct"][percentile] == pytest.approx(
                expected, abs=1e-6
            )


def test_work_thread_and_attachment_paths_remain_bounded() -> None:
    budgets = _load("budgets.json")
    deterministic = budgets["deterministic"]
    relative = budgets["relative"]
    before = _results(_load("baseline.json"))
    after = _results(_load("after.json"))

    work = after["work.initial_load_request_graph"]
    assert work["fixture"]["bounded_route_summary"] is True
    assert _p95(work, "api_requests") <= deterministic["work_initial_max_requests"]
    assert _p95(work) <= (
        _p95(before["work.initial_load_request_graph"])
        * relative["work_initial_p95_max_fraction_of_i2"]
    )

    narrative = after["work.run_narrative_update_isolation"]
    assert (
        _p95(narrative, "api_requests")
        <= deterministic["narrative_update_max_requests"]
    )
    assert (
        _p95(narrative, "projected_nodes")
        <= deterministic["narrative_update_max_projected_nodes"]
    )

    thread_list = after["thread.list_page"]
    thread_read = after["thread.read_page"]
    assert _p95(thread_list, "items") <= deterministic["thread_list_max_items"]
    assert _p95(thread_list, "has_more") == 1
    assert _p95(thread_read, "messages") <= deterministic["thread_read_max_messages"]
    assert _p95(thread_read, "has_more") == 1

    utf8 = after["attachments.valid_utf8_decode"]
    assert (
        _p95(utf8, "replacement_count")
        <= deterministic["attachment_max_replacement_count"]
    )
    assert _p95(utf8, "truncated") <= deterministic["attachment_max_truncated"]
    regression_pct = -float(utf8["change_pct"]["p95"])
    assert regression_pct <= relative["utf8_p95_max_regression_pct"]


def test_instruction_gateway_and_evidence_mechanisms_remain_bounded() -> None:
    budgets = _load("budgets.json")["deterministic"]
    after = _results(_load("after.json"))

    for workload_id in (
        "instructions.discovery_small",
        "instructions.discovery_large",
    ):
        instruction = after[workload_id]
        assert (
            _p95(instruction, "scanned_paths")
            <= budgets["instruction_max_scanned_paths"]
        )
        assert _p95(instruction, "sources") <= budgets["instruction_max_sources"]
        assert _p95(instruction, "truncated") == 0

    preflight = after["gateway.preflight"]
    assert (
        _p95(preflight, "discovery_calls")
        <= budgets["gateway_preflight_max_discovery_calls"]
    )
    assert _p95(preflight, "ready") == budgets["gateway_preflight_required_ready"]

    discovery = after["gateway.route_model_discovery"]
    assert (
        _p95(discovery, "transport_calls")
        <= budgets["gateway_route_discovery_max_transport_calls"]
    )
    assert (
        _p95(discovery, "routes") == budgets["gateway_route_discovery_required_routes"]
    )

    cold = after["gateway.sidecar_cold_start"]
    assert _p95(cold, "process_spawns") <= budgets["gateway_cold_max_process_spawns"]
    assert _p95(cold, "started") == budgets["gateway_cold_required_started"]

    warm = after["gateway.sidecar_warm_attach"]
    assert _p95(warm, "process_spawns") <= budgets["gateway_warm_max_process_spawns"]
    assert _p95(warm, "reused") == budgets["gateway_warm_required_reused"]

    evidence = after["evidence.local_export"]
    assert (
        _p95(evidence, "network_calls") <= budgets["local_evidence_max_network_calls"]
    )


def test_0_9_1_baseline_is_clean_and_matches_the_current_lock() -> None:
    baseline = _load("baseline_0_9_1.json")

    assert baseline["source_revision"] == "bfd1a936510a0f965a9e23758d1d27b97be42f83"
    assert baseline["source_dirty"] is False
    assert (
        baseline["lock_sha256"]
        == _load("cli_startup_baseline_0_9_1.json")["lock_sha256"]
    )


def test_relay_collector_counts_the_batch_list_and_single_read_paths(tmp_path) -> None:
    list_case, read_case = _relay_cases(tmp_path)

    list_case.before_each()
    listed = list_case.operation()
    list_counters = list_case.details(listed)
    read_case.before_each()
    read = read_case.operation()
    read_counters = read_case.details(read)

    assert list_counters == {
        "has_more": 1.0,
        "items": 50.0,
        "latest_run_batch_reads": 1.0,
        "latest_run_sessions": 50.0,
        "run_page_reads": 0.0,
        "session_page_reads": 1.0,
    }
    assert read_counters == {
        "has_more": 1.0,
        "message_page_reads": 1.0,
        "messages": 50.0,
        "run_page_reads": 1.0,
    }


def test_gateway_collector_separates_micro_and_loopback_http_cases(tmp_path) -> None:
    with ExitStack() as stack:
        cases = _gateway_cases(stack, tmp_path)
        micro = next(
            case for case in cases if case.id == "gateway.route_model_discovery"
        )
        integration = next(
            case for case in cases if case.id == "gateway.route_model_discovery_http"
        )

        integration.before_each()
        result = integration.operation()
        counters = integration.details(result)

    assert micro.measurement_kind == "micro"
    assert integration.measurement_kind == "integration"
    assert counters == {"http_requests": 3.0, "routes": 1.0}


def test_relative_budget_checker_enforces_thread_targets() -> None:
    report = {
        "results": [
            {
                "id": "thread.list_page",
                "latency_ms": {"p95": 80.0},
                "change_pct": {"p95": 70.0},
            },
            {
                "id": "thread.read_page",
                "latency_ms": {"p95": 12.0},
                "change_pct": {"p95": -9.0},
            },
        ]
    }
    budgets = {
        "relative": {
            "thread_list_p95_max_ms": 90.0,
            "thread_list_p95_min_improvement_pct": 60.0,
            "thread_read_p95_max_regression_pct": 10.0,
        }
    }

    _enforce_relative_budgets(report, budgets)
    report["results"][0]["change_pct"]["p95"] = 59.0

    with pytest.raises(ValueError, match="thread.list_page improvement"):
        _enforce_relative_budgets(report, budgets)
