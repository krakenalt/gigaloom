"""Structural compatibility contracts for the automation bounded context."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gigaloom"

LEGACY_MODULES = {
    "agents": "automation.agents.api",
    "arena": "automation.arena.api",
    "attention": "automation.attention.service",
    "authoring": "automation.agents.authoring",
    "evals": "automation.evaluations.api",
    "schedules": "automation.schedules.api",
    "workflow_catalog": "automation.workflows.catalog",
    "workflows": "automation.workflows.api",
}


@pytest.mark.parametrize(("legacy", "bounded"), LEGACY_MODULES.items())
def test_legacy_automation_modules_are_exact_bounded_aliases(
    legacy: str,
    bounded: str,
) -> None:
    legacy_module = importlib.import_module(f"gigaloom.{legacy}")
    bounded_module = importlib.import_module(f"gigaloom.{bounded}")

    assert legacy_module is bounded_module
    assert len((PACKAGE_ROOT / f"{legacy}.py").read_text().splitlines()) <= 30


def test_automation_modules_stay_bounded_and_avoid_concrete_runtime_storage() -> None:
    automation_root = PACKAGE_ROOT / "automation"
    forbidden_imports = (
        "gigaloom.runtime.db",
        "gigaloom.runtime.repositories",
        "gigaloom.runtime.store",
    )

    for source in sorted(automation_root.rglob("*.py")):
        assert len(source.read_text().splitlines()) <= 600, source
        tree = ast.parse(source.read_text(), filename=str(source))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        assert not any(
            target == forbidden or target.startswith(f"{forbidden}.")
            for target in imported
            for forbidden in forbidden_imports
        ), source
