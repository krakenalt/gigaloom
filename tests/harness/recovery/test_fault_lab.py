from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigaloom.diagnostics.fault_lab import (
    ActiveDataRootRejectedError,
    FaultFixtureId,
    FaultLabRunner,
    FaultScenarioStatus,
)


def test_fault_catalog_matches_versioned_fixture_manifest():
    payload = json.loads(
        (
            Path(__file__).parents[2] / "fixtures" / "recovery" / "fault_catalog.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == 1
    assert payload["fixtures"] == sorted(item.value for item in FaultFixtureId)


def test_all_fault_fixtures_reproduce_and_leave_no_sandbox_state(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    sandbox_parent = tmp_path / "sandboxes"
    sandbox_parent.mkdir()
    runner = FaultLabRunner(active_data_root=active)

    results = runner.run_all(sandbox_parent=sandbox_parent)

    assert len(results) == len(FaultFixtureId)
    assert all(item.status is FaultScenarioStatus.PASSED for item in results), results
    assert all(item.invariants for item in results)
    assert list(sandbox_parent.iterdir()) == []
    serialized = repr(results)
    assert str(tmp_path) not in serialized
    assert "fault fixture" not in serialized


def test_fault_results_are_byte_stable_across_disposable_runs(tmp_path):
    sandbox_parent = tmp_path / "sandboxes"
    sandbox_parent.mkdir()
    runner = FaultLabRunner()

    first = runner.run_all(sandbox_parent=sandbox_parent)
    second = runner.run_all(sandbox_parent=sandbox_parent)

    assert first == second
    assert [item.result_digest for item in first] == [
        item.result_digest for item in second
    ]


@pytest.mark.parametrize("relation", ("same", "ancestor", "descendant"))
def test_fault_lab_rejects_active_data_directory_overlap(tmp_path, relation):
    active = tmp_path / "active"
    active.mkdir()
    if relation == "same":
        sandbox_parent = active
    elif relation == "ancestor":
        sandbox_parent = tmp_path
    else:
        sandbox_parent = active / "nested"
        sandbox_parent.mkdir()

    with pytest.raises(ActiveDataRootRejectedError, match="active data"):
        FaultLabRunner(active_data_root=active).run(
            FaultFixtureId.JSONL_TAIL_TRUNCATED,
            sandbox_parent=sandbox_parent,
        )


def test_fault_lab_rejects_symlink_sandbox_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ActiveDataRootRejectedError, match="real directory"):
        FaultLabRunner().run(
            FaultFixtureId.JSONL_TAIL_TRUNCATED,
            sandbox_parent=link,
        )


def test_fault_invariants_cover_terminal_side_effect_and_cancel_safety(tmp_path):
    sandbox_parent = tmp_path / "sandboxes"
    sandbox_parent.mkdir()
    by_fixture = {
        item.fixture_id: item
        for item in FaultLabRunner().run_all(sandbox_parent=sandbox_parent)
    }

    assert {
        "no_stuck_running",
        "no_accidental_success",
        "no_duplicate_side_effect",
    } <= {
        item.invariant_id
        for item in by_fixture[FaultFixtureId.WORKER_DIES_AFTER_CLAIM].invariants
    }
    assert {
        "cancel_retained",
        "run_not_orphaned",
        "terminal_event_once",
    } <= {
        item.invariant_id
        for item in by_fixture[FaultFixtureId.CANCEL_BEFORE_TERMINAL_EVENT].invariants
    }
    assert "side_effect_exactly_once" in {
        item.invariant_id
        for item in by_fixture[
            FaultFixtureId.RESTART_AFTER_LEASE_BEFORE_SIDE_EFFECT
        ].invariants
    }
