"""Root package namespace guardrails."""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType


def test_root_namespace_matches_frozen_manifest(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    assert (
        architecture_checker.check_root_namespace(
            package_root,
            architecture_manifest,
        )
        == []
    )


def test_root_manifest_has_no_stale_compatibility_or_deviation_entries(
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    policy = architecture_manifest["root_namespace"]
    assert isinstance(policy, dict)

    actual_modules = {
        path.name
        for path in package_root.iterdir()
        if path.is_file() and (path.suffix == ".py" or path.name == "py.typed")
    }
    actual_contexts = {
        path.name
        for path in package_root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }

    for key, actual in (
        ("compatibility_module_groups", actual_modules),
        ("target_deviation_module_groups", actual_modules),
        ("compatibility_context_groups", actual_contexts),
        ("target_deviation_context_groups", actual_contexts),
    ):
        groups = policy[key]
        assert isinstance(groups, list)
        for group in groups:
            assert isinstance(group, dict)
            assert set(group["paths"]) <= actual


def test_root_compatibility_allowlist_contains_only_import_facades(
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    policy = architecture_manifest["root_namespace"]
    assert isinstance(policy, dict)
    groups = policy["compatibility_module_groups"]
    assert isinstance(groups, list)

    for group in groups:
        assert isinstance(group, dict)
        for relative in group["paths"]:
            path = package_root / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            business_definitions = [
                node
                for node in tree.body
                if isinstance(
                    node,
                    (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef),
                )
            ]
            assert business_definitions == [], relative


def test_target_deviations_reference_the_versioned_adr(
    architecture_manifest: dict[str, object],
    repository_root: Path,
) -> None:
    policy = architecture_manifest["root_namespace"]
    assert isinstance(policy, dict)
    for key in (
        "target_deviation_module_groups",
        "target_deviation_context_groups",
    ):
        groups = policy[key]
        assert isinstance(groups, list)
        for group in groups:
            assert isinstance(group, dict)
            adr = group["adr"]
            assert isinstance(adr, str)
            assert (repository_root / adr).is_file()


def test_stable_contexts_have_local_ownership_contracts(
    architecture_manifest: dict[str, object],
    package_root: Path,
) -> None:
    policy = architecture_manifest["root_namespace"]
    assert isinstance(policy, dict)
    required_headings = (
        "# Scope",
        "# Public API",
        "# Allowed imports",
        "# Forbidden imports",
        "# Persistence/security invariants",
        "# Performance budgets",
        "# Focused validation commands",
        "# Owner thread/CODEOWNERS",
    )
    for context in policy["target_contexts"]:
        path = package_root / context
        if not path.is_dir():
            continue
        contract = (path / "AGENTS.md").read_text(encoding="utf-8")
        for heading in required_headings:
            assert heading in contract, f"{context}/AGENTS.md: missing {heading}"


def test_new_root_business_module_is_rejected(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gigaloom"
    package_root.mkdir()
    (package_root / "unexpected_business.py").write_text(
        '"""Unapproved business module."""\n',
        encoding="utf-8",
    )

    assert architecture_checker.check_root_namespace(
        package_root,
        architecture_manifest,
    ) == ["unapproved root module: unexpected_business.py"]
