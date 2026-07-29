"""Compatibility and size contracts for the bounded MCP tools tree."""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "implementation_name"),
    (
        ("mcp", "tools.mcp.api"),
        ("external_mcp", "tools.mcp.external"),
        ("managed_mcp", "tools.mcp.managed"),
        ("managed_mcp_inventory", "tools.mcp.managed_inventory"),
        ("mcp_authoring", "tools.mcp.authoring"),
    ),
)
def test_legacy_mcp_module_is_bounded_context_alias(
    legacy_name: str,
    implementation_name: str,
) -> None:
    """Keep legacy module identity for imports and monkeypatch contracts."""
    legacy = importlib.import_module(f"gpt2giga_harness.{legacy_name}")
    implementation = importlib.import_module(f"gpt2giga_harness.{implementation_name}")

    assert legacy is implementation


def test_mcp_implementation_and_shims_fit_structural_limits() -> None:
    """Keep new modules bounded and old root paths implementation-free."""
    package_root = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / ("gpt2giga-harness")
        / "src"
        / "gpt2giga_harness"
    )
    implementation_root = package_root / "tools" / "mcp"

    assert (
        max(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in implementation_root.glob("*.py")
        )
        <= 600
    )
    for name in (
        "mcp.py",
        "external_mcp.py",
        "managed_mcp.py",
        "managed_mcp_inventory.py",
        "mcp_authoring.py",
    ):
        assert len((package_root / name).read_text(encoding="utf-8").splitlines()) <= 30


def test_canonical_mcp_modules_do_not_import_legacy_shims() -> None:
    """Keep compatibility aliases out of the canonical implementation graph."""
    legacy_modules = (
        "gpt2giga_harness.external_mcp",
        "gpt2giga_harness.managed_mcp",
        "gpt2giga_harness.managed_mcp_inventory",
        "gpt2giga_harness.mcp",
        "gpt2giga_harness.mcp_authoring",
    )
    implementation_root = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "gpt2giga-harness"
        / "src"
        / "gpt2giga_harness"
        / "tools"
        / "mcp"
    )

    for path in implementation_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for module_name in legacy_modules:
            assert f"from {module_name} import" not in source, (path, module_name)
            assert f"import {module_name}" not in source, (path, module_name)


@pytest.mark.parametrize(
    "modules",
    (
        ("gpt2giga_harness.tools.mcp", "gpt2giga_harness.integrations.api"),
        ("gpt2giga_harness.integrations.api", "gpt2giga_harness.tools.mcp"),
        (
            "gpt2giga_harness.managed_mcp_inventory",
            "gpt2giga_harness.external_mcp",
        ),
    ),
)
def test_mcp_and_integration_contexts_import_without_cycles(
    modules: tuple[str, ...],
) -> None:
    """Exercise both context import orders in a fresh interpreter."""
    script = "; ".join(f"import {module}" for module in modules)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
