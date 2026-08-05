"""Clean 0.9.1 performance and ACP route baseline contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "benchmarks" / "gigaloom_performance" / "workflow_0_9"
ACP_ROUTE = (
    ROOT
    / "tests"
    / "fixtures"
    / "acp"
    / "provider_bridge"
    / "openai_chat_completions_route.json"
)
BASE_REVISION = "bfd1a936510a0f965a9e23758d1d27b97be42f83"
PRIVACY = {
    "content_free": True,
    "external_network_accessed": False,
    "native_homes_accessed": False,
    "provider_traffic": False,
    "temporary_state_only": True,
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_clean_release_baselines_bind_the_exact_revision_and_environment() -> None:
    workflow = _load(EVIDENCE / "baseline_0_9_1.json")
    cli = _load(EVIDENCE / "cli_startup_baseline_0_9_1.json")

    assert workflow["source_revision"] == cli["source_revision"] == BASE_REVISION
    assert workflow["source_dirty"] is cli["source_dirty"] is False
    assert workflow["lock_sha256"] == cli["lock_sha256"]
    assert workflow["environment"] == cli["environment"]
    assert workflow["privacy"] == cli["privacy"] == PRIVACY
    assert workflow["samples_per_workload"] == 20
    assert cli["samples_per_command"] == 20
    assert cli["warmups_per_command"] == 3


def test_release_baseline_freezes_thread_and_cli_workloads() -> None:
    workflow = {
        item["id"]: item for item in _load(EVIDENCE / "baseline_0_9_1.json")["results"]
    }
    cli = {
        item["id"]: item
        for item in _load(EVIDENCE / "cli_startup_baseline_0_9_1.json")["results"]
    }

    thread_list = workflow["thread.list_page"]
    assert thread_list["fixture"] == {"retained_threads": 120, "page_limit": 50}
    assert thread_list["counters"]["items"]["p95"] == 50
    assert thread_list["latency_ms"]["p50"] > 0
    assert thread_list["latency_ms"]["p95"] > 0
    assert set(cli) == {
        "cli.version",
        "cli.root_help",
        "cli.agent_list_json",
        "cli.builtin_agent_help",
    }
    assert all(item["fixture"]["isolated_home"] is True for item in cli.values())


def test_acp_route_fixture_separates_transport_from_provider_protocol() -> None:
    fixture = _load(ACP_ROUTE)
    route = fixture["discovered_route"]

    assert fixture["transport_kind"] == "acp_stdio_v1"
    assert fixture["requested_agent_kind"] == "managed_acp"
    assert route["provider_protocol"] == "openai_chat_completions"
    assert route["provider_protocol"] != fixture["transport_kind"]
    assert fixture["expected_resolution"] == {
        "accepted": True,
        "provider_protocol_is_transport": False,
        "provider_default_allowed": False,
    }
    serialized = json.dumps(fixture, sort_keys=True).lower()
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_production_loc_baseline_covers_the_release_complexity_scope() -> None:
    baseline = _load(EVIDENCE / "production_loc_baseline_0_9_1.json")

    assert baseline["source_revision"] == BASE_REVISION
    assert baseline["totals"] == {"files": 34, "lines": 6712}
    assert sum(area["files"] for area in baseline["areas"].values()) == 34
    assert sum(area["lines"] for area in baseline["areas"].values()) == 6712


def test_release_complexity_scope_does_not_grow_past_the_baseline() -> None:
    baseline = _load(EVIDENCE / "production_loc_baseline_0_9_1.json")
    files = {
        path
        for pattern in baseline["scope"]
        for path in ROOT.glob(pattern)
        if path.is_file()
    }
    lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in files)

    assert len(files) <= baseline["totals"]["files"] + 1
    assert lines <= baseline["totals"]["lines"]
    assert not (ROOT / "src/gigaloom/harnesses/managed_acp_gateway.py").exists()
