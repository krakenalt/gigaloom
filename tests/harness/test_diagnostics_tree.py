"""Compatibility and size contracts for the bounded diagnostics tree."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


PACKAGE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "gpt2giga-harness"
    / "src"
    / "gpt2giga_harness"
)


@pytest.mark.parametrize(
    ("legacy_name", "implementation_name"),
    (
        ("doctor", "diagnostics.doctor.report"),
        ("compatibility_guardian", "diagnostics.compatibility.guardian"),
        ("product_inventory", "diagnostics.inventory.product"),
        ("product_capabilities", "diagnostics.inventory.capabilities"),
        ("performance_baseline", "diagnostics.performance.baseline"),
        ("runtime_performance_profile", "diagnostics.performance.runtime"),
        ("tui_performance_profile", "diagnostics.performance.tui"),
    ),
)
def test_legacy_diagnostic_module_is_bounded_context_alias(
    legacy_name: str,
    implementation_name: str,
) -> None:
    """Keep root import identity and monkeypatch contracts during migration."""
    prefix = "gpt2giga_harness."
    legacy = importlib.import_module(prefix + legacy_name)
    implementation = importlib.import_module(prefix + implementation_name)

    assert legacy is implementation


@pytest.mark.parametrize(
    "suffix",
    (
        ".runtime.profile",
        ".sessions.profile",
        ".surfaces.tui_render",
    ),
)
def test_legacy_performance_workload_is_canonical_alias(suffix: str) -> None:
    """Keep benchmark fixture imports on one canonical module identity."""
    legacy = importlib.import_module(f"gpt2giga_harness.performance_workloads{suffix}")
    implementation = importlib.import_module(
        f"gpt2giga_harness.diagnostics.performance.workloads{suffix}"
    )

    assert legacy is implementation


def test_legacy_performance_workload_facade_reexports_canonical_objects() -> None:
    """Keep public workload contracts identical across the package move."""
    legacy = importlib.import_module("gpt2giga_harness.performance_workloads")
    implementation = importlib.import_module(
        "gpt2giga_harness.diagnostics.performance.workloads"
    )

    assert legacy.WorkloadSpec is implementation.WorkloadSpec
    assert legacy.workload_contracts is implementation.workload_contracts


def test_diagnostic_implementations_and_shims_fit_structural_limits() -> None:
    """Keep canonical modules bounded and root compatibility implementation-free."""
    implementation_root = PACKAGE_ROOT / "diagnostics"
    assert (
        max(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in implementation_root.rglob("*.py")
        )
        <= 600
    )

    root_shims = (
        "compatibility_guardian.py",
        "doctor.py",
        "performance_baseline.py",
        "product_capabilities.py",
        "product_inventory.py",
        "runtime_performance_profile.py",
        "tui_performance_profile.py",
    )
    for relative_path in root_shims:
        assert (
            len((PACKAGE_ROOT / relative_path).read_text(encoding="utf-8").splitlines())
            <= 30
        )
    for path in (PACKAGE_ROOT / "performance_workloads").rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 20


def test_canonical_diagnostics_do_not_import_legacy_paths() -> None:
    """Keep root compatibility shims out of the canonical dependency graph."""
    legacy_modules = (
        "gpt2giga_harness.compatibility_guardian",
        "gpt2giga_harness.doctor",
        "gpt2giga_harness.performance_baseline",
        "gpt2giga_harness.performance_workloads",
        "gpt2giga_harness.product_capabilities",
        "gpt2giga_harness.product_inventory",
        "gpt2giga_harness.runtime_performance_profile",
        "gpt2giga_harness.tui_performance_profile",
    )

    for path in (PACKAGE_ROOT / "diagnostics").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for module_name in legacy_modules:
            assert f"from {module_name} import" not in source, (path, module_name)
            assert f"import {module_name}" not in source, (path, module_name)
