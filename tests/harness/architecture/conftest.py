"""Shared fixtures for architecture guardrails."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def architecture_checker(repository_root: Path) -> ModuleType:
    path = repository_root / "scripts" / "check_architecture.py"
    spec = importlib.util.spec_from_file_location("_architecture_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load architecture checker from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def package_root(repository_root: Path) -> Path:
    return repository_root / "src" / "gigaloom"


@pytest.fixture(scope="session")
def architecture_manifest(
    repository_root: Path,
    architecture_checker: ModuleType,
) -> dict[str, object]:
    path = repository_root / "architecture" / "module-budgets.json"
    return architecture_checker.load_manifest(path)
