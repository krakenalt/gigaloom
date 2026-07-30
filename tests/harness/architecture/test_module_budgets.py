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


def test_legacy_budgets_are_exact_documented_exceptions(
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    policy = architecture_manifest["module_budgets"]
    assert isinstance(policy, dict)
    hard_limit = policy["python_module_hard_limit"]
    assert isinstance(hard_limit, int)
    legacy_modules = policy["legacy_modules"]
    assert isinstance(legacy_modules, list)

    actual_legacy = {
        path.relative_to(package_root).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in package_root.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > hard_limit
    }
    manifest_legacy = {entry["path"]: entry["max_lines"] for entry in legacy_modules}
    assert manifest_legacy == actual_legacy
    for entry in legacy_modules:
        assert entry["owner"]
        assert entry["removal_gate"]
        assert entry["reason"]


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
