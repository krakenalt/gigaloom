"""Compatibility and dependency checks for stable shared contracts."""

from __future__ import annotations

import ast
from importlib import import_module
from pathlib import Path

import gigaloom
from gigaloom import contracts
from gigaloom.core import instrumentation, paths, redaction


LEGACY_TYPE_EXPORTS = {
    "AdapterCapabilitySupport",
    "AdapterSupportLevel",
    "AttachmentTransportSupport",
    "Availability",
    "AvailabilityStatus",
    "ExecutionTransport",
    "GIGACHAT_BUILTIN_TOOLS",
    "GigaChatApiMode",
    "GigaChatBuiltinTool",
    "HarnessCapability",
    "HarnessChatMessage",
    "HarnessContext",
    "HarnessEvent",
    "HarnessEventType",
    "HarnessInvocationMode",
    "HarnessRequest",
    "HarnessResult",
    "HarnessSpec",
    "HeadlessContinuationStrategy",
    "REDACTED",
    "SECRET_ENV_NAMES",
    "SECRET_KEY_PARTS",
    "availability_to_dict",
    "emit_event",
    "event_to_dict",
    "parse_api_mode",
    "parse_builtin_tools",
    "parse_capability",
    "redact_secrets",
    "result_to_dict",
    "spec_capability_values",
    "spec_to_dict",
}
ROOT_EXPORTS = {
    "__version__",
    "Availability",
    "AvailabilityStatus",
    "GigaChatApiMode",
    "HarnessCapability",
    "HarnessChatMessage",
    "HarnessRegistry",
    "HarnessRequest",
    "HarnessResult",
    "HarnessSpec",
    "create_default_registry",
    "emit_event",
}
SHIMS = {
    "config.py",
    "instrumentation.py",
    "safe_paths.py",
    "types.py",
}


def test_legacy_shared_exports_resolve_to_canonical_objects() -> None:
    legacy_types = import_module("gigaloom.types")
    assert LEGACY_TYPE_EXPORTS <= set(vars(legacy_types))
    assert legacy_types.HarnessRequest is contracts.HarnessRequest
    assert legacy_types.ExecutionTransport is contracts.ExecutionTransport
    assert legacy_types.HarnessInvocationMode is contracts.HarnessInvocationMode
    assert legacy_types.redact_secrets is redaction.redact_secrets
    assert contracts.HarnessRequest.__module__ == "gigaloom.types"
    assert contracts.ExecutionTransport.__module__ == "gigaloom.execution"
    assert contracts.HarnessInvocationMode.__module__ == "gigaloom.native.models"

    legacy_config = import_module("gigaloom.config")
    legacy_paths = import_module("gigaloom.safe_paths")
    legacy_instrumentation = import_module("gigaloom.instrumentation")
    assert legacy_config.HarnessConfig is contracts.HarnessConfig
    assert legacy_paths.resolve_path_within is paths.resolve_path_within
    assert legacy_instrumentation.record_duration is instrumentation.record_duration


def test_root_init_exports_are_preserved_and_canonical() -> None:
    assert set(gigaloom.__all__) == ROOT_EXPORTS
    assert gigaloom.Availability is contracts.Availability
    assert gigaloom.GigaChatApiMode is contracts.GigaChatApiMode
    assert gigaloom.HarnessRequest is contracts.HarnessRequest
    assert gigaloom.emit_event is contracts.emit_event


def test_root_compatibility_shims_are_small_and_logic_free(
    package_root: Path,
) -> None:
    for relative_path in SHIMS:
        path = package_root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 30
        assert not any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            for node in tree.body
        )


def test_core_and_contract_import_graph_is_acyclic(package_root: Path) -> None:
    module_paths = {
        ".".join(path.relative_to(package_root).with_suffix("").parts): path
        for context in ("core", "contracts")
        for path in (package_root / context).glob("*.py")
    }
    dependencies: dict[str, set[str]] = {module: set() for module in module_paths}
    for module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            prefix = "gigaloom."
            if node.module.startswith(prefix):
                target = node.module.removeprefix(prefix)
                if target in module_paths:
                    dependencies[module].add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"shared import cycle through {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in dependencies:
        visit(module)
