"""Compatibility and size contracts for the bounded Skills tree."""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "implementation_name"),
    (
        ("builtin_skills", "skills.builtin"),
        ("external_skills", "skills.external"),
        ("portable_skills", "skills.portable"),
        ("skill_library", "skills.library"),
        ("skills_catalog_proxy", "skills.catalog_proxy.server"),
        ("skills_catalog_proxy_client", "skills.catalog_proxy.client"),
    ),
)
def test_legacy_skills_module_is_bounded_context_alias(
    legacy_name: str,
    implementation_name: str,
) -> None:
    """Keep legacy module identity for imports and monkeypatch contracts."""
    legacy = importlib.import_module(f"gigaloom.{legacy_name}")
    implementation = importlib.import_module(f"gigaloom.{implementation_name}")

    assert legacy is implementation


def test_skills_implementation_and_shims_fit_structural_limits() -> None:
    """Keep new modules bounded and old root paths implementation-free."""
    package_root = Path(__file__).resolve().parents[2] / "src" / "gigaloom"
    implementation_root = package_root / "skills"

    assert (
        max(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in implementation_root.rglob("*.py")
        )
        <= 600
    )
    for name in (
        "builtin_skills.py",
        "external_skills.py",
        "portable_skills.py",
        "skill_library.py",
        "skills_catalog_proxy.py",
        "skills_catalog_proxy_client.py",
    ):
        assert len((package_root / name).read_text(encoding="utf-8").splitlines()) <= 30


def test_canonical_skills_modules_do_not_import_legacy_shims() -> None:
    """Keep compatibility aliases out of the canonical implementation graph."""
    legacy_modules = (
        "gigaloom.builtin_skills",
        "gigaloom.external_skills",
        "gigaloom.portable_skills",
        "gigaloom.skill_library",
        "gigaloom.skills_catalog_proxy",
        "gigaloom.skills_catalog_proxy_client",
    )
    implementation_root = (
        Path(__file__).resolve().parents[2] / "src" / "gigaloom" / "skills"
    )

    for path in implementation_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for module_name in legacy_modules:
            assert f"from {module_name} import" not in source, (path, module_name)
            assert f"import {module_name}" not in source, (path, module_name)


@pytest.mark.parametrize(
    "modules",
    (
        ("gigaloom.skills.api", "gigaloom.integrations.api"),
        ("gigaloom.integrations.api", "gigaloom.skills.api"),
        ("gigaloom.skills.api", "gigaloom.tools.mcp"),
        (
            "gigaloom.skills_catalog_proxy_client",
            "gigaloom.skills_catalog_proxy",
        ),
    ),
)
def test_skills_context_imports_without_cycles(
    modules: tuple[str, ...],
) -> None:
    """Exercise Skills and adjacent context import orders in a fresh interpreter."""
    script = "; ".join(f"import {module}" for module in modules)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
