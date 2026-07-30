"""AST-only first-party import boundary checks."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import ModuleType


def test_current_imports_follow_frozen_direction(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    assert architecture_manifest["import_boundaries"]["temporary_violations"] == []
    assert (
        architecture_checker.check_import_boundaries(
            package_root,
            architecture_manifest,
        )
        == []
    )


def test_domain_cannot_import_presentation_surface(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gigaloom"
    runtime = package_root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "jobs.py").write_text(
        "from gigaloom.ui import app\n",
        encoding="utf-8",
    )
    manifest = deepcopy(architecture_manifest)
    manifest["import_boundaries"]["temporary_violations"] = []

    assert architecture_checker.check_import_boundaries(
        package_root,
        manifest,
    ) == ["runtime/jobs.py:1: domains-do-not-import-surfaces forbids gigaloom.ui"]


def test_relative_import_is_resolved_without_importing_code(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gigaloom"
    contracts = package_root / "contracts"
    ui = package_root / "ui"
    contracts.mkdir(parents=True)
    ui.mkdir()
    (contracts / "models.py").write_text(
        "from ..ui import app\n",
        encoding="utf-8",
    )
    manifest = deepcopy(architecture_manifest)
    manifest["import_boundaries"]["temporary_violations"] = []

    assert architecture_checker.check_import_boundaries(
        package_root,
        manifest,
    ) == ["contracts/models.py:1: contracts-depend-only-on-core forbids gigaloom.ui"]


def test_cross_context_import_uses_public_facade(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gigaloom"
    runtime = package_root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "jobs.py").write_text(
        "from gigaloom.sessions.store import SessionStore\n",
        encoding="utf-8",
    )
    manifest = deepcopy(architecture_manifest)
    manifest["import_boundaries"]["temporary_violations"] = []

    assert architecture_checker.check_import_boundaries(
        package_root,
        manifest,
    ) == [
        "runtime/jobs.py:1: cross-context-imports-use-public-facades forbids "
        "gigaloom.sessions.store"
    ]


def test_stale_import_allowlist_entry_is_rejected(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gigaloom"
    package_root.mkdir()
    manifest = deepcopy(architecture_manifest)
    manifest["import_boundaries"]["temporary_violations"] = [
        {
            "source": "runtime/removed.py",
            "target": "gigaloom.sessions.store",
            "owner": "T04",
            "removal_gate": "T04-C1",
        }
    ]

    assert architecture_checker.check_import_boundaries(
        package_root,
        manifest,
    ) == [
        "stale temporary import violation: runtime/removed.py -> "
        "gigaloom.sessions.store"
    ]
