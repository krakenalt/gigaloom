#!/usr/bin/env python3
"""Audit release literals and implementation-history labels in tracked files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPOSITORY_ROOT / "release" / "literal-policy.toml"
MAX_ENTRY_BYTES = 8 * 1024 * 1024


class LiteralAuditError(RuntimeError):
    """Raised when the repository literal audit cannot complete safely."""


@dataclass(frozen=True, order=True)
class Violation:
    """One stable repository-literal violation."""

    path: str
    kind: str
    rule: str
    line: int | None = None

    def render(self) -> str:
        """Render one concise actionable diagnostic."""
        location = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{location}: {self.kind} ({self.rule})"


@dataclass(frozen=True)
class LabelRule:
    """Compiled implementation-label rule."""

    id: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class VersionCategory:
    """One semantic class of accepted release literal projections."""

    name: str
    path_globs: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    """Validated literal policy used by the scanner."""

    allow_label_globs: tuple[str, ...]
    label_rules: tuple[LabelRule, ...]
    path_rules: tuple[LabelRule, ...]
    canonical_source: str
    canonical_field: str
    current_version: str
    python_version: str
    migration_target: str
    version_categories: tuple[VersionCategory, ...]


@dataclass(frozen=True)
class AuditResult:
    """Content-free result of one complete repository scan."""

    files_scanned: int
    label_hits: int
    release_literal_hits: int
    version_categories: tuple[tuple[str, int], ...]
    violations: tuple[Violation, ...]


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    """Load and validate the standard-library TOML policy."""
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LiteralAuditError(f"cannot read literal policy: {path}") from exc
    if payload.get("schema_version") != 1:
        raise LiteralAuditError("literal policy schema_version must be 1")
    labels = _mapping(payload.get("implementation_labels"), "implementation_labels")
    release = _mapping(payload.get("release_identity"), "release_identity")
    rules = _rules(labels.get("patterns"), "implementation label patterns")
    path_rules = _rules(labels.get("path_patterns"), "implementation path patterns")
    categories = tuple(
        VersionCategory(
            name=_required_text(item.get("name"), "release category name"),
            path_globs=_string_tuple(
                item.get("path_globs"), "release category path_globs"
            ),
        )
        for item in _mapping_sequence(
            release.get("categories"), "release identity categories"
        )
    )
    if len({category.name for category in categories}) != len(categories):
        raise LiteralAuditError("release identity category names must be unique")
    return Policy(
        allow_label_globs=_string_tuple(
            labels.get("allow_path_globs"), "implementation label allow_path_globs"
        ),
        label_rules=rules,
        path_rules=path_rules,
        canonical_source=_required_text(
            release.get("canonical_source"), "release canonical_source"
        ),
        canonical_field=_required_text(
            release.get("canonical_field"), "release canonical_field"
        ),
        current_version=_required_text(
            release.get("current_version"), "release current_version"
        ),
        python_version=_required_text(
            release.get("python_version"), "release python_version"
        ),
        migration_target=_required_text(
            release.get("migration_target"), "release migration_target"
        ),
        version_categories=categories,
    )


def audit_repository(root: Path, policy: Policy) -> AuditResult:
    """Scan tracked and visible untracked files under one repository root."""
    root = root.resolve()
    tracked = _git_paths(root)
    entries = tuple((relative, _read_entry(root, relative)) for relative in tracked)
    return audit_entries(root, policy, entries)


def audit_entries(
    root: Path,
    policy: Policy,
    entries: Iterable[tuple[str, bytes]],
) -> AuditResult:
    """Audit supplied normalized entries; exposed for hermetic tests."""
    violations: set[Violation] = set()
    files_scanned = 0
    label_hits = 0
    release_literal_hits = 0
    category_counts = {category.name: 0 for category in policy.version_categories}
    release_needles = tuple(
        dict.fromkeys((policy.current_version, policy.python_version))
    )
    for relative, data in entries:
        files_scanned += 1
        path_allowed = _matches_any(relative, policy.allow_label_globs)
        path_text = relative.replace(os.sep, "/")
        if not path_allowed:
            for rule in policy.path_rules:
                if rule.pattern.search(path_text):
                    label_hits += 1
                    violations.add(
                        Violation(relative, "implementation label in path", rule.id)
                    )
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not path_allowed:
            for rule in policy.label_rules:
                for match in rule.pattern.finditer(text):
                    label_hits += 1
                    violations.add(
                        Violation(
                            relative,
                            "implementation label in text",
                            rule.id,
                            _line_for(text, match.start()),
                        )
                    )
        for needle in release_needles:
            start = 0
            while True:
                offset = text.find(needle, start)
                if offset < 0:
                    break
                release_literal_hits += 1
                categories = tuple(
                    category
                    for category in policy.version_categories
                    if _matches_any(relative, category.path_globs)
                )
                if not categories:
                    violations.add(
                        Violation(
                            relative,
                            "unclassified release literal",
                            needle,
                            _line_for(text, offset),
                        )
                    )
                else:
                    for category in categories:
                        category_counts[category.name] += 1
                start = offset + len(needle)
    violations.update(_canonical_violations(root, policy))
    return AuditResult(
        files_scanned=files_scanned,
        label_hits=label_hits,
        release_literal_hits=release_literal_hits,
        version_categories=tuple(sorted(category_counts.items())),
        violations=tuple(sorted(violations)),
    )


def _canonical_violations(root: Path, policy: Policy) -> set[Violation]:
    path = root / policy.canonical_source
    if not path.is_file():
        return {
            Violation(
                policy.canonical_source,
                "missing canonical release source",
                policy.migration_target,
            )
        }
    try:
        if path.suffix == ".toml":
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            raise LiteralAuditError("canonical release source must be TOML or JSON")
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LiteralAuditError("cannot decode canonical release source") from exc
    value = (
        payload.get(policy.canonical_field) if isinstance(payload, Mapping) else None
    )
    if value == policy.current_version:
        return set()
    return {
        Violation(
            policy.canonical_source,
            "canonical release value mismatch",
            f"expected {policy.current_version}",
        )
    }


def _git_paths(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise LiteralAuditError(f"git ls-files failed: {detail or result.returncode}")
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def _read_entry(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise LiteralAuditError(f"cannot inspect tracked path: {relative}") from exc
    if stat.S_ISLNK(mode):
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    if not stat.S_ISREG(mode):
        raise LiteralAuditError(f"tracked path is not a regular file: {relative}")
    if path.stat().st_size > MAX_ENTRY_BYTES:
        raise LiteralAuditError(f"tracked file exceeds scan limit: {relative}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise LiteralAuditError(f"cannot read tracked path: {relative}") from exc


def _rules(value: Any, label: str) -> tuple[LabelRule, ...]:
    compiled: list[LabelRule] = []
    for item in _mapping_sequence(value, label):
        rule_id = _required_text(item.get("id"), f"{label} id")
        expression = _required_text(item.get("regex"), f"{label} regex")
        try:
            pattern = re.compile(expression)
        except re.error as exc:
            raise LiteralAuditError(f"invalid {label} regex {rule_id!r}") from exc
        compiled.append(LabelRule(rule_id, pattern))
    if len({rule.id for rule in compiled}) != len(compiled):
        raise LiteralAuditError(f"{label} ids must be unique")
    return tuple(compiled)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiteralAuditError(f"{label} must be a table")
    return value


def _mapping_sequence(value: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LiteralAuditError(f"{label} must be an array of tables")
    items = tuple(value)
    if not items or not all(isinstance(item, Mapping) for item in items):
        raise LiteralAuditError(f"{label} must contain tables")
    return items  # type: ignore[return-value]


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LiteralAuditError(f"{label} must be an array")
    items = tuple(value)
    if not items or not all(isinstance(item, str) and item for item in items):
        raise LiteralAuditError(f"{label} must contain non-empty strings")
    return items  # type: ignore[return-value]


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiteralAuditError(f"{label} must be non-empty text")
    return value.strip()


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _line_for(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _summary(result: AuditResult) -> dict[str, Any]:
    return {
        "files_scanned": result.files_scanned,
        "implementation_label_hits": result.label_hits,
        "release_literal_hits": result.release_literal_hits,
        "release_literal_categories": dict(result.version_categories),
        "violations": [violation.render() for violation in result.violations],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the repository audit and return non-zero on policy violations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy.resolve())
        result = audit_repository(args.root.resolve(), policy)
    except LiteralAuditError as exc:
        print(f"repository literal audit failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(_summary(result), indent=2, sort_keys=True))
    elif result.violations:
        for violation in result.violations:
            print(violation.render(), file=sys.stderr)
    else:
        print(
            "repository literal audit passed "
            f"({result.files_scanned} files, "
            f"{result.release_literal_hits} classified release literals)"
        )
    return 1 if result.violations and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
