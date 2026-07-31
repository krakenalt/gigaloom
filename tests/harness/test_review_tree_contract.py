"""Structural compatibility contracts for the review bounded context."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gigaloom"

LEGACY_MODULES = {
    "generated_files": "review.artifacts.generated",
    "handoff_capsules": "review.handoffs.api",
    "pr_artifacts": "review.artifacts.api",
    "promotions": "review.promotions.api",
    "provenance": "review.provenance",
    "reviewed_evidence": "review.evidence",
    "support_bundle": "review.support",
    "trace_replay": "review.replay.api",
}


@pytest.mark.parametrize(("legacy", "bounded"), LEGACY_MODULES.items())
def test_legacy_review_modules_are_exact_bounded_aliases(
    legacy: str,
    bounded: str,
) -> None:
    legacy_module = importlib.import_module(f"gigaloom.{legacy}")
    bounded_module = importlib.import_module(f"gigaloom.{bounded}")

    assert legacy_module is bounded_module
    assert len((PACKAGE_ROOT / f"{legacy}.py").read_text().splitlines()) <= 30


def test_review_modules_stay_bounded_and_avoid_concrete_runtime_storage() -> None:
    review_root = PACKAGE_ROOT / "review"
    forbidden_imports = (
        "gigaloom.runtime.db",
        "gigaloom.runtime.repositories",
        "gigaloom.runtime.store",
    )

    for source in sorted(review_root.rglob("*.py")):
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


def test_review_layers_separate_preview_apply_and_support_export() -> None:
    assert (PACKAGE_ROOT / "review/promotions/preview.py").is_file()
    assert (PACKAGE_ROOT / "review/promotions/apply.py").is_file()
    assert (PACKAGE_ROOT / "review/artifacts/preview.py").is_file()
    assert (PACKAGE_ROOT / "review/artifacts/apply.py").is_file()
    assert (PACKAGE_ROOT / "review/support.py").is_file()
