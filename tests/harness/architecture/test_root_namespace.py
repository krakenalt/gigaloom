"""Root package namespace guardrails."""

from __future__ import annotations

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


def test_new_root_business_module_is_rejected(
    architecture_checker: ModuleType,
    architecture_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "gpt2giga_harness"
    package_root.mkdir()
    (package_root / "unexpected_business.py").write_text(
        '"""Unapproved business module."""\n',
        encoding="utf-8",
    )

    assert architecture_checker.check_root_namespace(
        package_root,
        architecture_manifest,
    ) == ["unapproved root module: unexpected_business.py"]
