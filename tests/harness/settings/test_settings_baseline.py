from __future__ import annotations

import json
from pathlib import Path


BASELINE_PATH = (
    Path(__file__).parents[3]
    / "benchmarks"
    / "gigaloom_performance"
    / "settings"
    / "baseline.json"
)


def test_settings_initial_load_baseline_is_complete_content_free_evidence():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == "gigaloom.settings-initial-load.v1"
    assert len(baseline["source_commit"]) == 40
    assert baseline["privacy"] == {
        "content_free": True,
        "external_network_accessed": False,
        "native_homes_accessed": False,
        "provider_traffic": False,
        "temporary_state_only": True,
    }
    backend = baseline["backend"]
    assert backend["cold"]["wall"]["samples"] == 5
    assert backend["warm"]["wall"]["samples"] == 5
    for phase in ("cold", "warm"):
        counts = backend[phase]["operation_counts"]
        assert counts["filesystem_reads"]["p50"] >= 0
        assert counts["harness_availability_calls"]["p50"] >= 1
        assert counts["subprocesses_started"]["p50"] >= 1
        assert set(backend[phase]["component_p50_ms"]) >= {
            "build_mcp_inventory",
            "harness.availability",
            "provider_registry.list",
            "resolve_project",
            "settings_store.load",
        }
    assert backend["fixture_bounds"] == {
        "harnesses": 5,
        "mcp_servers": 0,
        "providers": 0,
    }

    browser = baseline["browser"]
    assert browser["cold_first_section"]["samples"] == 5
    assert browser["interaction_latency"]["samples"] == 5
    assert browser["api_requests_before_first_section"]["minimum"] >= 4
    assert browser["settings_route_chunk_bytes"] > 0
