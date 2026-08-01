from __future__ import annotations

import importlib
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from gigaloom import cli
from gigaloom.performance_baseline import (
    CI_SMOKE_BUDGETS_MS,
    DETAIL_REFERENCE_BUDGETS_MS,
    FIXTURE_SET_VERSION,
    REGRESSION_BASELINE_ID,
    REPORT_ARTIFACT_MAX_BYTES,
    REPORT_RETENTION_DAYS,
    REQUIRED_WORKLOADS,
    SCHEMA_VERSION,
    run_performance_baseline,
    write_performance_report,
)
from gigaloom.performance_workloads import (
    REQUIRED_WORKLOAD_FAMILIES,
    WorkloadSpec,
    discover_workloads,
    workload_contracts,
)
from gigaloom.performance_workloads.sessions import events
from gigaloom.performance_workloads.sessions.profile import (
    _measure_case,
    run_session_storage_scaling_baseline,
)
from gigaloom.performance_workloads.runtime.profile import (
    run_runtime_scaling_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_performance_report_contract(report, *, profile):
    assert report["baseline"] == {
        "id": REGRESSION_BASELINE_ID,
        "profile": profile,
        "schema_version": report["schema_version"],
        "fixture_set_version": report["fixture_set_version"],
        "comparison": "tracked_absolute_budget",
        "budget_source": "versioned_profile_constants",
        "ci_blocking_metrics": (
            list(CI_SMOKE_BUDGETS_MS) if profile == "ci-smoke" else []
        ),
        "external_latency_failure_policy": "excluded",
    }
    assert report["artifact_policy"] == {
        "max_bytes": REPORT_ARTIFACT_MAX_BYTES[profile],
        "retention_days": REPORT_RETENTION_DAYS[profile],
        "bounded_samples_max": 100,
        "content_free": True,
    }
    fingerprint = report["environment"]["fingerprint"]
    assert fingerprint["algorithm"] == "sha256"
    assert len(fingerprint["value"]) == 64
    assert fingerprint["fields"] == sorted(fingerprint["fields"])
    assert "sqlite" in fingerprint["fields"]


def test_cli_import_does_not_load_testclient_backend():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, warnings; "
                "from starlette.exceptions import StarletteDeprecationWarning; "
                "warnings.simplefilter('error', StarletteDeprecationWarning); "
                "import gigaloom.cli; "
                "assert 'fastapi.testclient' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_performance_baseline_imports_without_posix_resource_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "class BlockResource:\n"
                "    def find_spec(self, fullname, path=None, target=None):\n"
                "        if fullname == 'resource':\n"
                "            raise ModuleNotFoundError(fullname)\n"
                "        return None\n"
                "sys.meta_path.insert(0, BlockResource()); "
                "from gigaloom import performance_baseline as baseline; "
                "sample = baseline._measure(lambda: {}); "
                "assert sample.rss_bytes == 0; "
                "assert sample.input_blocks == 0; "
                "assert sample.output_blocks == 0"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_performance_baseline_is_bounded_content_free_and_machine_readable():
    report = run_performance_baseline(samples=2)

    _assert_performance_report_contract(report, profile="ci-smoke")
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["fixture_set_version"] == FIXTURE_SET_VERSION
    assert report["samples_per_probe"] == 2
    assert report["privacy"] == {
        "content_captured": False,
        "secrets_captured": False,
        "native_homes_accessed": False,
        "provider_traffic": False,
        "network_accessed": False,
        "temporary_state_only": True,
    }
    assert report["measurement_contract"]["required_workloads"] == list(
        REQUIRED_WORKLOADS
    )
    assert report["measurement_contract"]["workload_registry"] == workload_contracts()
    assert {result["id"] for result in report["results"]} == set(CI_SMOKE_BUDGETS_MS)
    for result in report["results"]:
        assert set(result["percentiles_ms"]) == {"p50", "p95", "p99"}
        assert result["regression_gate"]["blocking"] is True
        assert result["regression_gate"]["classification"] == "environment_stable_ci"
        assert result["io"]["io_wait_ms"] is None
        assert result["optimization_target_ms"] is None
        assert "samples" not in result
    assert (
        report["measurement_contract"][
            "provider_or_external_network_latency_is_blocking"
        ]
        is False
    )


def test_workload_registry_is_deterministic_and_covers_required_families():
    first = discover_workloads()
    second = discover_workloads()

    assert first == second
    assert tuple(workload.family for workload in first) == REQUIRED_WORKLOAD_FAMILIES
    assert len({workload.id for workload in first}) == len(first)
    assert all(isinstance(workload, WorkloadSpec) for workload in first)
    assert workload_contracts() == [workload.as_contract() for workload in first]


def test_workload_discovery_accepts_new_module_without_central_inventory(
    tmp_path,
    monkeypatch,
):
    package_name = "performance_extension_fixture"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    declarations = (
        ("zeta.py", "extension.zeta", "extension/zeta"),
        ("alpha.py", "extension.alpha", "extension/alpha"),
    )
    for filename, workload_id, family in declarations:
        (package / filename).write_text(
            "\n".join(
                (
                    "from gigaloom.performance_workloads import WorkloadSpec",
                    "",
                    "WORKLOADS = (",
                    "    WorkloadSpec(",
                    f"        id={workload_id!r},",
                    f"        family={family!r},",
                    "        profiles=('local-detail',),",
                    "        variants=('fixture',),",
                    "        required_metrics=('wall_ms',),",
                    "        required_counters=('calls',),",
                    "        future_gate='fixture',",
                    "    ),",
                    ")",
                    "",
                )
            ),
            encoding="utf-8",
        )

    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    discovered = discover_workloads(
        package_name=package_name,
        required_families=(),
    )

    assert [workload.id for workload in discovered] == [
        "extension.alpha",
        "extension.zeta",
    ]


def test_session_storage_case_resets_mutable_fixture_between_samples(tmp_path):
    steady_append_factory = events.case_factories()[-2]

    result = _measure_case(steady_append_factory(tmp_path), samples=2)

    assert result["samples"] == 2
    assert result["details"]["appended_events"]["p95"] == 100
    assert result["counters"]["fsync_calls"]["p95"] == 100
    assert result["counters"]["index_reads"]["p95"] == 0


@pytest.mark.parametrize("samples", (0, 101))
def test_session_storage_profile_rejects_unbounded_sample_counts(samples):
    with pytest.raises(ValueError, match="samples must be between 1 and 100"):
        run_session_storage_scaling_baseline(samples=samples)


@pytest.mark.parametrize("samples", (0, 101))
def test_runtime_scaling_profile_rejects_unbounded_sample_counts(samples):
    with pytest.raises(ValueError, match="samples must be between 1 and 100"):
        run_runtime_scaling_baseline(samples=samples)


def test_runtime_scaling_profile_captures_required_content_free_fixtures():
    report = run_runtime_scaling_baseline(samples=1)

    assert report["schema_version"] == "gigaloom.runtime-scaling-baseline.v1"
    assert report["fixture_set_version"] == "runtime-workload.v1"
    assert report["profile"] == "runtime-detail"
    assert len(report["source_commit"]) == 40
    assert report["samples_per_case"] == 1
    assert report["environment"]["fingerprint"]["algorithm"] == "sha256"
    assert len(report["environment"]["fingerprint"]["value"]) == 64
    assert report["privacy"] == {
        "content_captured": False,
        "secrets_captured": False,
        "private_paths_captured": False,
        "sql_parameter_values_captured": False,
        "native_homes_accessed": False,
        "provider_traffic": False,
        "network_accessed": False,
        "temporary_state_only": True,
    }
    assert report["measurement_contract"] == {
        "fixture_setup_in_measured_window": False,
        "fixture_setup": "direct_content_free_canonical_state",
        "production_store_used_in_measured_window": True,
        "queue_size": 10_000,
        "incompatible_percent": 90,
        "worker_counts": [2, 8],
        "revision_scales": [100, 1_000, 10_000, 50_000],
        "maintenance_cases": [
            "heartbeat",
            "idle",
            "idle_minute",
            "schedule",
            "recovery",
            "reconcile",
        ],
        "absolute_wall_time_is_ci_blocking": False,
        "algorithmic_counters_are_ci_stable": True,
        "query_plan_sql_retained": False,
        "query_plan_parameter_values_retained": False,
    }
    assert len(report["results"]) == 15
    by_metric = {item["id"]: item for item in report["results"]}
    assert (
        by_metric["runtime.queue.claim.incompatible_90_percent"]["details"][
            "compatible_candidate_position"
        ]["p95"]
        == 9_001
    )
    assert (
        by_metric["runtime.queue.claim.compatible_at_window_end"]["details"][
            "compatible_candidate_position"
        ]["p95"]
        == 9_001
    )
    assert (
        by_metric["runtime.queue.claim.compatible_at_window_end"]["details"][
            "compatible_queue_position"
        ]["p95"]
        == 10_000
    )
    assert (
        by_metric["runtime.queue.claim.compatible_at_window_end"]["fixture"][
            "incompatible_jobs"
        ]
        == 9_000
    )
    for workers in (2, 8):
        result = by_metric[f"runtime.queue.claim.workers_{workers}"]
        assert result["details"]["workers"]["p95"] == workers
        assert result["counters"]["claimed_jobs"]["p95"] == workers
        assert result["counters"]["duplicate_claims"]["p95"] == 0
    for scale in (100, 1_000, 10_000, 50_000):
        result = by_metric[f"runtime.revisions.runs_center.rows_{scale}"]
        assert result["counters"]["rows_parsed"]["p95"] == scale
    for variant in (
        "heartbeat",
        "idle",
        "idle_minute",
        "schedule",
        "recovery",
        "reconcile",
    ):
        result = by_metric[f"runtime.worker.lifecycle.{variant}"]
        assert result["measured_window"] == "operation_only"
        assert result["regression_gate"]["blocking"] is False
    idle_minute = by_metric["runtime.worker.lifecycle.idle_minute"]
    heartbeat = by_metric["runtime.worker.lifecycle.heartbeat"]
    assert heartbeat["details"]["attempt_leases"]["p95"] == 1
    assert heartbeat["counters"]["sqlite_connections"]["p95"] == 1
    assert heartbeat["counters"]["sqlite_writes"]["p95"] == 2
    assert (
        idle_minute["counters"]["sqlite_connections"]["p95"]
        <= idle_minute["details"]["baseline_sql_connections"]["p95"] * 0.4
    )
    assert (
        idle_minute["counters"]["sqlite_statements"]["p95"]
        <= idle_minute["details"]["baseline_sql_statements"]["p95"] * 0.4
    )
    counter_names = {
        "claimed_jobs",
        "duplicate_claims",
        "maintenance_cycles",
        "rows_parsed",
        "sqlite_connections",
        "sqlite_reads",
        "sqlite_statements",
        "sqlite_writes",
        "wakeups",
    }
    assert all(set(item["counters"]) == counter_names for item in report["results"])

    serialized_plans = json.dumps(report["query_plans"], sort_keys=True)
    assert len(report["query_plans"]) == 8
    assert "2026-01-01" not in serialized_plans
    assert "fixture" not in serialized_plans
    for plan in report["query_plans"]:
        assert set(plan) == {"id", "sql_sha256", "parameter_count", "steps"}
        assert len(plan["sql_sha256"]) == 64
        assert plan["steps"]
        assert all(
            set(step) == {"select_id", "parent_id", "detail"} for step in plan["steps"]
        )


def test_local_detail_profile_keeps_bounded_content_free_samples():
    report = run_performance_baseline(samples=1, profile="local-detail")

    _assert_performance_report_contract(report, profile="local-detail")
    assert {result["id"] for result in report["results"]} == set(
        DETAIL_REFERENCE_BUDGETS_MS
    )
    for result in report["results"]:
        assert len(result["samples"]) == 1
        assert set(result["samples"][0]) == {
            "wall_ms",
            "cpu_ms",
            "rss_bytes",
            "input_blocks",
            "output_blocks",
            "stages_ms",
        }
        if result["id"] not in CI_SMOKE_BUDGETS_MS:
            assert result["regression_gate"] == {
                "blocking": False,
                "classification": "scheduled_or_opt_in_detail",
                "budget_ms": {
                    "percentile": "p95",
                    "p95": DETAIL_REFERENCE_BUDGETS_MS[result["id"]],
                },
            }
    storage = report["session_storage_baseline"]
    assert (
        len(json.dumps(report, ensure_ascii=True).encode())
        < REPORT_ARTIFACT_MAX_BYTES["local-detail"]
    )
    assert storage["schema_version"] == "gigaloom.session-storage-baseline.v1"
    assert storage["fixture_set_version"] == "session-workload.v1"
    assert storage["samples_per_case"] == 1
    assert storage["privacy"] == report["privacy"]
    assert storage["measurement_contract"] == {
        "fixture_setup_in_measured_window": False,
        "fixture_setup": "direct_content_free_canonical_state",
        "production_store_used_in_measured_window": True,
        "durability_disabled_in_measured_window": False,
        "absolute_wall_time_is_ci_blocking": False,
        "algorithmic_counters_are_ci_stable": True,
        "catalog_scales": [10, 100, 1_000],
        "message_history": 5_000,
        "message_tail": 20,
        "run_scales": [10, 100, 1_000],
        "event_history": 50_000,
        "steady_events": 100,
        "burst_events": 500,
    }
    assert len(storage["results"]) == 21
    by_storage_metric = {item["id"]: item for item in storage["results"]}
    assert all(
        item["measured_window"] == "operation_only"
        and item["regression_gate"]
        == {
            "blocking": False,
            "classification": "reference_wall_time_algorithmic_counters",
        }
        for item in storage["results"]
    )
    counters = {
        "atomic_replaces",
        "bytes_read",
        "bytes_written",
        "files_opened",
        "fsync_calls",
        "index_reads",
        "manifest_reads",
        "rows_parsed",
        "sqlite_connections",
        "sqlite_statements",
    }
    assert all(set(item["counters"]) == counters for item in storage["results"])
    assert (
        by_storage_metric["sessions.catalog.create_1000"]["counters"]["index_reads"][
            "p95"
        ]
        == 0
    )
    assert (
        by_storage_metric["sessions.catalog.first_page_cold_1000"]["counters"][
            "manifest_reads"
        ]["p95"]
        == 1_000
    )
    assert (
        by_storage_metric["sessions.catalog.first_page_cold_1000"]["counters"][
            "index_reads"
        ]["p95"]
        == 0
    )
    assert (
        by_storage_metric["sessions.catalog.first_page_warm_1000"]["counters"][
            "manifest_reads"
        ]["p95"]
        == 0
    )
    assert (
        by_storage_metric["sessions.messages.latest_20_of_5000"]["counters"][
            "rows_parsed"
        ]["p95"]
        == 5_000
    )
    assert {
        by_storage_metric[f"sessions.catalog.create_{scale}"]["counters"][
            "bytes_written"
        ]["p95"]
        for scale in (10, 100, 1_000)
    } == {609}
    for scale in (10, 100, 1_000):
        update = by_storage_metric[f"sessions.runs.update_1_of_{scale}"]
        assert update["counters"]["rows_parsed"]["p95"] == 1
        assert update["counters"]["atomic_replaces"]["p95"] == 2
        assert update["counters"]["fsync_calls"]["p95"] == 1
    assert (
        by_storage_metric["sessions.events.direct_lookup_last_of_50000"]["counters"][
            "rows_parsed"
        ]["p95"]
        == 50_000
    )
    assert (
        by_storage_metric["sessions.events.append_steady_100"]["counters"][
            "fsync_calls"
        ]["p95"]
        == 100
    )
    assert (
        by_storage_metric["sessions.events.append_steady_100"]["counters"][
            "index_reads"
        ]["p95"]
        == 0
    )
    assert (
        by_storage_metric["sessions.events.append_burst_500"]["counters"][
            "fsync_calls"
        ]["p95"]
        == 500
    )
    assert (
        by_storage_metric["sessions.events.append_burst_500"]["counters"][
            "index_reads"
        ]["p95"]
        == 0
    )


def test_runtime_detail_profile_is_ranked_bounded_and_content_free():
    report = run_performance_baseline(samples=1, profile="runtime-detail")

    _assert_performance_report_contract(report, profile="runtime-detail")
    assert report["schema_version"] == "gigaloom.runtime-performance-profile.v3"
    assert report["fixture_set_version"] == "runtime-performance.v1"
    assert len(report["source_commit"]) == 40
    assert report["privacy"] == {
        "content_captured": False,
        "secrets_captured": False,
        "native_homes_accessed": False,
        "provider_traffic": False,
        "network_accessed": False,
        "temporary_state_only": True,
    }
    assert report["measurement_contract"]["optimization_performed"] is True
    assert report["measurement_contract"]["runtime_measurement_authorized"] is True
    assert report["measurement_contract"]["filesystem_scan_repair_authorized"] is True
    assert report["missing_coverage"] == {}
    assert (
        report["runtime_scaling_baseline"]["schema_version"]
        == "gigaloom.runtime-scaling-baseline.v1"
    )
    assert report["status"] == "passed"
    metrics = {item["id"] for item in report["results"]}
    assert {
        "worker_idle_cycle",
        "worker_idle_loop",
        "worker_wakeup_signal",
        "worker_active_echo",
        "queue_claim_one",
        "queue_claim_many",
        "sqlite_lock_contention",
        "retry_requeue",
        "expired_lease_recovery",
        "api_defaults",
        "api_session_events",
        "sse_terminal_attach",
        "session_run_update",
    } <= metrics
    by_metric = {item["id"]: item for item in report["results"]}
    assert by_metric["worker_active_echo"]["sqlite"]["observed"] is True
    assert by_metric["worker_active_echo"]["sqlite"]["writes"]["p95"] > 0
    assert by_metric["queue_claim_many"]["details"]["claimed_jobs"]["p95"] == 16
    assert by_metric["queue_claim_many"]["details"]["duplicate_claims"]["p95"] == 0
    assert by_metric["worker_idle_loop"]["details"]["cycles"]["p95"] >= 2
    assert (
        by_metric["worker_idle_loop"]["details"]["projected_steady_cycles_per_minute"][
            "p95"
        ]
        <= 65
    )
    assert by_metric["worker_idle_loop"]["target_status"] == "within_target"
    assert by_metric["worker_wakeup_signal"]["target_status"] == "within_target"
    assert by_metric["worker_wakeup_signal"]["details"]["delivered"]["p95"] == 1
    assert by_metric["sse_terminal_attach"]["details"]["frames"]["p95"] >= 1
    assert by_metric["runtime_reconcile"]["details"]["outbox_failed"]["p95"] == 0
    assert by_metric["session_run_update"]["details"]["retained_runs"]["p95"] == 16
    assert by_metric["session_run_update"]["details"]["updated_runs"]["p95"] == 1
    assert [item["rank"] for item in report["ranked_bottlenecks"]] == list(
        range(1, len(report["ranked_bottlenecks"]) + 1)
    )
    assert {item["target_status"] for item in report["results"]} == {
        "within_target",
        "reference_only_not_selected",
    }
    decisions = {item["id"]: item["status"] for item in report["candidate_repairs"]}
    assert decisions == {
        "demand_driven_worker_wakeup": "implemented_within_budget",
        "conflict_aware_worker_concurrency": "not_selected_by_runtime_policy",
        "ranked_request_hot_path_repairs": (
            "bounded_filesystem_scan_repair_implemented"
        ),
    }


@pytest.mark.parametrize("samples", (0, 101))
def test_performance_baseline_rejects_unbounded_sample_counts(samples):
    with pytest.raises(ValueError, match="samples must be between 1 and 100"):
        run_performance_baseline(samples=samples)


def test_performance_cli_writes_private_report(tmp_path, capsys):
    output = tmp_path / "report.json"

    assert (
        cli.main(
            [
                "benchmark",
                "performance",
                "--samples",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "Wrote private performance report" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("profile", "ci_blocking_metrics", "expected_exit_code"),
    (
        ("ci-smoke", ["session_projection"], 1),
        ("local-detail", [], 0),
        ("runtime-detail", [], 0),
    ),
)
def test_performance_cli_only_fails_for_ci_blocking_metrics(
    monkeypatch,
    capsys,
    profile,
    ci_blocking_metrics,
    expected_exit_code,
):
    monkeypatch.setattr(
        cli,
        "run_performance_baseline",
        lambda **_kwargs: {
            "status": "failed",
            "baseline": {"ci_blocking_metrics": ci_blocking_metrics},
        },
    )

    assert (
        cli.main(["benchmark", "performance", "--profile", profile])
        == expected_exit_code
    )
    assert '"status": "failed"' in capsys.readouterr().out


def test_performance_report_rejects_artifacts_over_profile_limit(tmp_path):
    output = tmp_path / "oversized.json"
    report = run_performance_baseline(samples=1)
    report["oversized_fixture"] = "x" * REPORT_ARTIFACT_MAX_BYTES["ci-smoke"]

    with pytest.raises(ValueError, match="limit is 65536"):
        write_performance_report(output, report)

    assert not output.exists()


def test_performance_workflows_split_ci_and_detailed_profiles():
    ci = (REPO_ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    nightly = (REPO_ROOT / ".github/workflows/nightly-smoke.yaml").read_text(
        encoding="utf-8"
    )

    assert (
        "giga benchmark performance --profile ci-smoke --samples 5 "
        "--output performance-artifacts/ci-smoke.json"
    ) in ci
    assert "retention-days: 7" in ci
    for profile in ("local-detail", "runtime-detail"):
        assert f"giga benchmark performance --profile {profile}" in nightly
    assert "retention-days: 14" in nightly


def test_performance_cli_writes_private_runtime_profile(tmp_path, capsys):
    output = tmp_path / "runtime-report.json"

    assert (
        cli.main(
            [
                "benchmark",
                "performance",
                "--profile",
                "runtime-detail",
                "--samples",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "gigaloom.runtime-performance-profile.v3"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "Wrote private performance report" in capsys.readouterr().out
