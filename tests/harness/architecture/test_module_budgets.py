"""Ratcheting Python module-size budgets."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import ModuleType


def test_current_modules_fit_ratcheting_budgets(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    assert (
        architecture_checker.check_module_budgets(
            package_root,
            architecture_manifest,
        )
        == []
    )


def test_legacy_module_cannot_grow(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gpt2giga_harness"
    package_root.mkdir()
    (package_root / "legacy.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    manifest = deepcopy(architecture_manifest)
    manifest["module_budgets"]["legacy_modules"] = [
        {"path": "legacy.py", "max_lines": 2}
    ]

    assert architecture_checker.check_module_budgets(
        package_root,
        manifest,
    ) == ["legacy.py: 3 lines exceeds legacy ceiling 2"]


def test_new_module_obeys_hard_limit(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gpt2giga_harness"
    context = package_root / "sessions"
    context.mkdir(parents=True)
    (context / "new_module.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    manifest = deepcopy(architecture_manifest)
    manifest["module_budgets"]["python_module_hard_limit"] = 2
    manifest["module_budgets"]["legacy_modules"] = []

    assert architecture_checker.check_module_budgets(
        package_root,
        manifest,
    ) == ["sessions/new_module.py: 3 lines exceeds hard limit 2"]
