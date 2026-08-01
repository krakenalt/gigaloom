from __future__ import annotations

import json
from pathlib import Path

from gigaloom.ui.services.settings_projections import (
    MAX_SETTINGS_CACHE_ENTRIES,
    MAX_SETTINGS_MCP_ERRORS,
    MAX_SETTINGS_MCP_HISTORY_BYTES,
    MAX_SETTINGS_MCP_HISTORY_ROWS,
    MAX_SETTINGS_MCP_SERVERS,
)


EVIDENCE_ROOT = (
    Path(__file__).parents[3] / "benchmarks" / "gigaloom_performance" / "settings"
)


def test_settings_evidence_meets_relative_performance_ratchets():
    baseline = _read("baseline.json")
    after = _read("after.json")
    budgets = _read("budgets.json")["relative"]

    warm_fraction = (
        after["backend"]["settings.summary.warm"]["wall"]["p50_ms"]
        / baseline["backend"]["warm"]["wall"]["p50_ms"]
    )
    first_content_fraction = (
        after["browser"]["first_content"]["p50_ms"]
        / baseline["browser"]["cold_first_section"]["p50_ms"]
    )
    javascript_fraction = (
        after["browser"]["settings_javascript"]["maximum_bytes"]
        / baseline["browser"]["settings_route_chunk_bytes"]
    )
    interaction_fraction = (
        after["browser"]["interaction_latency"]["p50_ms"]
        / baseline["browser"]["interaction_latency"]["p50_ms"]
    )

    assert after["ratios_to_i1_baseline"] == {
        "cold_summary_fraction": round(
            after["backend"]["settings.summary.cold"]["wall"]["p50_ms"]
            / baseline["backend"]["cold"]["wall"]["p50_ms"],
            6,
        ),
        "first_content_fraction": round(first_content_fraction, 6),
        "initial_settings_javascript_fraction": round(javascript_fraction, 6),
        "interaction_latency_fraction": round(interaction_fraction, 6),
        "warm_summary_fraction": round(warm_fraction, 6),
    }

    assert warm_fraction <= budgets["warm_summary_max_fraction_of_legacy_warm"]
    assert first_content_fraction <= budgets["first_content_max_fraction_of_baseline"]
    assert (
        javascript_fraction
        <= budgets["initial_settings_javascript_max_fraction_of_baseline"]
    )
    assert (
        interaction_fraction <= budgets["interaction_latency_max_fraction_of_baseline"]
    )


def test_settings_evidence_meets_deterministic_initial_load_gates():
    after = _read("after.json")
    budgets = _read("budgets.json")["deterministic"]
    browser = after["browser"]
    cold = after["backend"]["settings.summary.cold"]
    warm = after["backend"]["settings.summary.warm"]

    assert (
        browser["required_settings_requests"]["maximum"]
        <= budgets["max_required_settings_requests"]
    )
    assert (
        browser["initial_section_requests"]["maximum"]
        <= budgets["initial_section_requests"]
    )
    assert (
        browser["initial_non_near_section_chunks"]["maximum"]
        <= budgets["initial_non_near_section_chunks"]
    )
    for phase in (cold, warm):
        counts = phase["operation_counts"]
        assert (
            counts["native_or_provider_probe_calls"]["maximum"]
            <= budgets["initial_native_or_provider_probe_calls"]
        )
        assert counts["subprocesses_started"]["maximum"] == 0


def test_settings_bounds_match_the_enforced_backend_limits():
    budgets = _read("budgets.json")["deterministic"]

    assert budgets["max_cache_entries"] == MAX_SETTINGS_CACHE_ENTRIES
    assert budgets["max_mcp_servers"] == MAX_SETTINGS_MCP_SERVERS
    assert budgets["max_mcp_errors"] == MAX_SETTINGS_MCP_ERRORS
    assert budgets["max_mcp_history_bytes"] == MAX_SETTINGS_MCP_HISTORY_BYTES
    assert budgets["max_mcp_history_rows"] == MAX_SETTINGS_MCP_HISTORY_ROWS


def test_settings_after_evidence_is_complete_and_content_free():
    after = _read("after.json")

    assert after["schema_version"] == "gigaloom.settings-performance-after.v1"
    assert len(after["source_commit"]) == 40
    assert after["privacy"] == {
        "content_free": True,
        "external_network_accessed": False,
        "native_homes_accessed": False,
        "provider_traffic": False,
        "temporary_state_only": True,
    }
    assert after["measurement"]["wall_time_portable"] is False
    assert "relative ratchets" in after["measurement"]["variance_note"]
    for workload in ("settings.summary.cold", "settings.summary.warm"):
        assert after["backend"][workload]["wall"]["samples"] == 5
        assert len(after["backend"][workload]["samples_ms"]) == 5
    assert after["browser"]["first_content"]["samples"] == 5
    assert len(after["browser"]["samples"]) == 5


def _read(name: str):
    return json.loads((EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
