#!/usr/bin/env python3
"""Check ratcheting package-layout and import-boundary constraints."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_NAME = "gigaloom"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOT = REPOSITORY_ROOT / "src" / PACKAGE_NAME
DEFAULT_MANIFEST = REPOSITORY_ROOT / "architecture" / "module-budgets.json"


@dataclass(frozen=True)
class ImportRecord:
    """One first-party import discovered without importing application code."""

    source: str
    target: str
    line: int


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and minimally validate the versioned architecture manifest."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported architecture manifest schema")
    if manifest.get("package") != PACKAGE_NAME:
        raise ValueError(f"manifest package must be {PACKAGE_NAME!r}")
    return manifest


def _grouped_paths(
    groups: list[dict[str, Any]],
    *,
    required_metadata: tuple[str, ...] = (),
) -> set[str]:
    paths: set[str] = set()
    for group in groups:
        if not group.get("owner") or not group.get("removal_gate"):
            raise ValueError("root namespace groups need owner and removal_gate")
        missing_metadata = [
            field for field in required_metadata if not group.get(field)
        ]
        if missing_metadata:
            joined = ", ".join(missing_metadata)
            raise ValueError(f"root namespace groups need {joined}")
        for path in group.get("paths", []):
            if path in paths:
                raise ValueError(f"duplicate root namespace path: {path}")
            paths.add(path)
    return paths


def check_root_namespace(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Reject new root modules or contexts outside the frozen target tree."""
    policy = manifest["root_namespace"]
    permanent_modules = set(policy["permanent_modules"])
    compatibility_modules = _grouped_paths(
        policy["compatibility_module_groups"],
        required_metadata=("compatibility_contract",),
    )
    deviation_modules = _grouped_paths(
        policy["target_deviation_module_groups"],
        required_metadata=("adr", "reason"),
    )
    allowed_modules = permanent_modules | compatibility_modules | deviation_modules

    permanent_contexts = set(policy["target_contexts"])
    compatibility_contexts = _grouped_paths(
        policy["compatibility_context_groups"],
        required_metadata=("compatibility_contract",),
    )
    deviation_contexts = _grouped_paths(
        policy["target_deviation_context_groups"],
        required_metadata=("adr", "reason"),
    )
    allowed_contexts = permanent_contexts | compatibility_contexts | deviation_contexts

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

    violations = [
        f"unapproved root module: {name}"
        for name in sorted(actual_modules - allowed_modules)
    ]
    violations.extend(
        f"unapproved root context: {name}"
        for name in sorted(actual_contexts - allowed_contexts)
    )
    return violations


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def check_module_budgets(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Enforce legacy ceilings and the hard limit for all other modules."""
    policy = manifest["module_budgets"]
    hard_limit = int(policy["python_module_hard_limit"])
    legacy_budgets: dict[str, int] = {}
    for entry in policy["legacy_modules"]:
        path = entry["path"]
        if path in legacy_budgets:
            raise ValueError(f"duplicate legacy module budget: {path}")
        legacy_budgets[path] = int(entry["max_lines"])

    violations: list[str] = []
    actual_modules: set[str] = set()
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        actual_modules.add(relative)
        line_count = _line_count(path)
        limit = legacy_budgets.get(relative, hard_limit)
        if line_count > limit:
            budget_kind = (
                "legacy ceiling" if relative in legacy_budgets else "hard limit"
            )
            violations.append(
                f"{relative}: {line_count} lines exceeds {budget_kind} {limit}"
            )
    violations.extend(
        f"stale legacy module budget: {relative}"
        for relative in sorted(set(legacy_budgets) - actual_modules)
    )
    return violations


def _module_parts(source: Path, package_root: Path) -> tuple[list[str], bool]:
    relative = source.relative_to(package_root).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return [PACKAGE_NAME, *parts], is_package


def _absolute_from_target(
    node: ast.ImportFrom,
    module_parts: list[str],
    *,
    is_package: bool,
) -> str | None:
    if node.level == 0:
        return node.module
    current_package = module_parts if is_package else module_parts[:-1]
    keep = len(current_package) - (node.level - 1)
    if keep < 1:
        return None
    base = current_package[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _iter_first_party_imports(
    package_root: Path,
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for source in sorted(package_root.rglob("*.py")):
        relative = source.relative_to(package_root).as_posix()
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        module_parts, is_package = _module_parts(source, package_root)
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _absolute_from_target(
                    node,
                    module_parts,
                    is_package=is_package,
                )
                if base:
                    targets.append(base)
                    if base == PACKAGE_NAME:
                        targets.extend(f"{base}.{alias.name}" for alias in node.names)
            for target in targets:
                if target == PACKAGE_NAME or target.startswith(f"{PACKAGE_NAME}."):
                    records.append(
                        ImportRecord(
                            source=relative,
                            target=target,
                            line=node.lineno,
                        )
                    )
    return records


def _context_name(path: str) -> str:
    context = path.split("/", maxsplit=1)[0]
    return context[:-3] if context.endswith(".py") else context


def _target_context(target: str) -> str | None:
    parts = target.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE_NAME:
        return None
    return parts[1]


def _allowlist_match(
    record: ImportRecord,
    allowlist: list[dict[str, Any]],
) -> int | None:
    for index, entry in enumerate(allowlist):
        if record.source == entry["source"] and (
            record.target == entry["target"]
            or record.target.startswith(f"{entry['target']}.")
        ):
            return index
    return None


def check_import_boundaries(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Parse imports with the stdlib AST and enforce dependency direction."""
    policy = manifest["import_boundaries"]
    rules = policy["rules"]
    allowlist = policy["temporary_violations"]
    for entry in allowlist:
        if not entry.get("owner") or not entry.get("removal_gate"):
            raise ValueError("temporary import violations need owner and removal_gate")
        if not entry.get("source") or not entry.get("target"):
            raise ValueError("temporary import violations need source and target")
        if any(character in entry["source"] for character in "*?["):
            raise ValueError("temporary import violations need exact source paths")
    violations: list[str] = []
    used_allowlist_entries: set[int] = set()
    for record in _iter_first_party_imports(package_root):
        source_context = _context_name(record.source)
        target_context = _target_context(record.target)
        if target_context is None:
            continue
        for rule in rules:
            if source_context not in rule["sources"]:
                continue
            target_contexts = rule.get("target_contexts")
            if target_contexts is not None and target_context not in set(
                target_contexts
            ):
                continue
            if rule.get("cross_context_only") and source_context == target_context:
                continue
            allowed = rule.get("allowed_targets")
            forbidden = set(rule.get("forbidden_targets", []))
            is_violation = (
                allowed is not None and target_context not in set(allowed)
            ) or target_context in forbidden
            allowed_modules = rule.get("allowed_target_modules")
            if allowed_modules is not None:
                target_parts = record.target.split(".")
                target_module = target_parts[2] if len(target_parts) > 2 else None
                is_violation = is_violation or (
                    target_module is not None
                    and target_module not in set(allowed_modules)
                )
            if is_violation:
                allowlist_match = _allowlist_match(record, allowlist)
                if allowlist_match is None:
                    violations.append(
                        f"{record.source}:{record.line}: {rule['id']} forbids "
                        f"{record.target}"
                    )
                else:
                    used_allowlist_entries.add(allowlist_match)
    for index, entry in enumerate(allowlist):
        if index not in used_allowlist_entries:
            violations.append(
                "stale temporary import violation: "
                f"{entry['source']} -> {entry['target']}"
            )
    return violations


def check_architecture(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Run every architecture check and return deterministic diagnostics."""
    if not package_root.is_dir():
        return [f"package root does not exist: {package_root}"]
    return [
        *check_root_namespace(package_root, manifest),
        *check_module_budgets(package_root, manifest),
        *check_import_boundaries(package_root, manifest),
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main() -> int:
    """Run architecture checks from the command line."""
    args = _build_parser().parse_args()
    try:
        manifest = load_manifest(args.manifest)
        violations = check_architecture(args.package, manifest)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        print(f"architecture check failed: {exc}")
        return 2
    if violations:
        print("\n".join(violations))
        return 1
    print("architecture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
