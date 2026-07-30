#!/usr/bin/env python3
"""Reject removed GigaLoom identifiers in source and release artifacts."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
from typing import Literal
import warnings
import zipfile


DENIED_IDENTIFIERS = (
    "gpt2giga_harness",
    "packages/gpt2giga-harness",
    "cockpit_v2",
    "cockpit-v2",
    "@gpt2giga/harness-cockpit-v2",
    "gpt2giga-cockpit",
    "GIGALOOM_COCKPIT_OUTPUT",
    "agent_workbench.",
    "gpt2giga-harness-v",
    "gigaloom-v",
)
_POLICY_PATH = "scripts/check_legacy_identifiers.py"
_HISTORICAL_PATHS = frozenset(
    {
        "CHANGELOG.md",
        "CHANGELOG_en.md",
        "release/0.6-native-operator-baseline.md",
        "release/adr/2026-07-30-multi-registry-release-identity.md",
    }
)
_SOURCE_ALLOWLIST = frozenset({_POLICY_PATH, *_HISTORICAL_PATHS})
_MAX_ENTRY_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
ArtifactKind = Literal["wheel", "sdist", "npm"]


class LegacyIdentifierError(RuntimeError):
    """Raised when a source or artifact scan cannot complete safely."""


@dataclass(frozen=True, order=True)
class Violation:
    """One denied identifier occurrence."""

    scope: str
    path: str
    identifier: str
    kind: Literal["path", "text", "ast-import"]
    line: int | None = None

    def render(self) -> str:
        """Render one stable, actionable diagnostic."""
        location = self.path if self.line is None else f"{self.path}:{self.line}"
        return (
            f"{self.scope}:{location}: denied {self.kind} identifier "
            f"{self.identifier!r}"
        )


def _git_tracked_paths(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise LegacyIdentifierError(
            f"git ls-files failed: {diagnostic or result.returncode}"
        )
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def _read_tracked_entry(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise LegacyIdentifierError(
            f"cannot inspect tracked path {relative!r}"
        ) from exc
    if stat.S_ISLNK(mode):
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    if not stat.S_ISREG(mode):
        raise LegacyIdentifierError(f"tracked path is not a regular file: {relative}")
    if path.stat().st_size > _MAX_ENTRY_BYTES:
        raise LegacyIdentifierError(f"tracked file exceeds scan limit: {relative}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise LegacyIdentifierError(f"cannot read tracked path {relative!r}") from exc


def _normalized_archive_path(raw: str, kind: ArtifactKind) -> str:
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise LegacyIdentifierError(f"unsafe {kind} archive path: {raw!r}")
    parts = candidate.parts
    if kind == "wheel":
        normalized = parts
    elif kind == "npm":
        if not parts or parts[0] != "package":
            raise LegacyIdentifierError(f"npm member is outside package/: {raw!r}")
        normalized = parts[1:]
    else:
        if len(parts) < 2:
            raise LegacyIdentifierError(f"sdist member has no root directory: {raw!r}")
        normalized = parts[1:]
    return PurePosixPath(*normalized).as_posix()


def _line_for(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _ast_import_hits(path: str, data: bytes) -> dict[str, int]:
    if not path.endswith(".py"):
        return {}
    try:
        source = data.decode("utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=path)
    except (UnicodeDecodeError, SyntaxError):
        return {}
    hits: dict[str, int] = {}
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = (node.module,)
        for module in modules:
            for identifier in DENIED_IDENTIFIERS:
                prefix = identifier.rstrip(".")
                if module == prefix or module.startswith(f"{prefix}."):
                    hits.setdefault(identifier, node.lineno)
    return hits


def scan_entry(scope: str, path: str, data: bytes) -> tuple[Violation, ...]:
    """Scan one normalized file path and its bytes."""
    violations: set[Violation] = set()
    encoded_path = path.encode("utf-8", errors="surrogateescape")
    for identifier in DENIED_IDENTIFIERS:
        needle = identifier.encode("utf-8")
        if needle in encoded_path:
            violations.add(
                Violation(
                    scope=scope,
                    path=path,
                    identifier=identifier,
                    kind="path",
                )
            )
        offset = data.find(needle)
        if offset >= 0:
            violations.add(
                Violation(
                    scope=scope,
                    path=path,
                    identifier=identifier,
                    kind="text",
                    line=_line_for(data, offset),
                )
            )
    for identifier, line in _ast_import_hits(path, data).items():
        violations.add(
            Violation(
                scope=scope,
                path=path,
                identifier=identifier,
                kind="ast-import",
                line=line,
            )
        )
    return tuple(sorted(violations))


def _allowed_source_violation(violation: Violation) -> bool:
    return violation.path in _SOURCE_ALLOWLIST


def check_source_tree(root: Path) -> tuple[Violation, ...]:
    """Scan all Git-tracked source paths with exact historical exceptions."""
    root = root.resolve()
    tracked = _git_tracked_paths(root)
    tracked_set = set(tracked)
    missing_allowlist = sorted(_SOURCE_ALLOWLIST - tracked_set)
    if missing_allowlist:
        raise LegacyIdentifierError(
            "legacy identifier allowlist paths are missing: "
            + ", ".join(missing_allowlist)
        )
    used_allowlist: set[str] = set()
    violations: list[Violation] = []
    for relative in tracked:
        data = _read_tracked_entry(root, relative)
        for violation in scan_entry("source", relative, data):
            if _allowed_source_violation(violation):
                used_allowlist.add(relative)
            else:
                violations.append(violation)
    stale_allowlist = sorted(_SOURCE_ALLOWLIST - used_allowlist)
    if stale_allowlist:
        raise LegacyIdentifierError(
            "legacy identifier allowlist entries are stale: "
            + ", ".join(stale_allowlist)
        )
    return tuple(sorted(violations))


def _check_zip(path: Path, kind: ArtifactKind) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                normalized = _normalized_archive_path(info.filename, kind)
                if info.is_dir():
                    data = b""
                else:
                    if info.file_size > _MAX_ENTRY_BYTES:
                        raise LegacyIdentifierError(
                            f"{kind} member exceeds scan limit: {normalized}"
                        )
                    total += info.file_size
                    if total > _MAX_ARCHIVE_BYTES:
                        raise LegacyIdentifierError(
                            f"{kind} archive exceeds total scan limit"
                        )
                    data = archive.read(info)
                violations.extend(scan_entry(kind, normalized, data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise LegacyIdentifierError(f"cannot scan {kind} archive {path}") from exc
    return tuple(
        sorted(
            violation
            for violation in violations
            if not _allowed_source_violation(violation)
        )
    )


def _check_tar(path: Path, kind: ArtifactKind) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    total = 0
    try:
        with tarfile.open(path, mode="r:*") as archive:
            for member in archive.getmembers():
                normalized = _normalized_archive_path(member.name, kind)
                data = b""
                if member.isfile():
                    if member.size > _MAX_ENTRY_BYTES:
                        raise LegacyIdentifierError(
                            f"{kind} member exceeds scan limit: {normalized}"
                        )
                    total += member.size
                    if total > _MAX_ARCHIVE_BYTES:
                        raise LegacyIdentifierError(
                            f"{kind} archive exceeds total scan limit"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise LegacyIdentifierError(
                            f"cannot read {kind} member: {normalized}"
                        )
                    data = stream.read()
                violations.extend(scan_entry(kind, normalized, data))
    except (OSError, tarfile.TarError) as exc:
        raise LegacyIdentifierError(f"cannot scan {kind} archive {path}") from exc
    return tuple(
        sorted(
            violation
            for violation in violations
            if not _allowed_source_violation(violation)
        )
    )


def check_artifact(path: Path, kind: ArtifactKind) -> tuple[Violation, ...]:
    """Scan one built wheel, sdist, or npm tarball."""
    path = path.resolve()
    if not path.is_file():
        raise LegacyIdentifierError(f"{kind} artifact does not exist: {path}")
    if kind == "wheel":
        return _check_zip(path, kind)
    return _check_tar(path, kind)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--npm-tarball", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run requested scans and return non-zero on policy violations."""
    args = _parser().parse_args(argv)
    requested = (
        args.source_root,
        args.wheel,
        args.sdist,
        args.npm_tarball,
    )
    if not any(requested):
        raise LegacyIdentifierError("at least one scan target is required")
    violations: list[Violation] = []
    scopes: list[str] = []
    if args.source_root is not None:
        scopes.append("source")
        violations.extend(check_source_tree(args.source_root))
    for value, kind in (
        (args.wheel, "wheel"),
        (args.sdist, "sdist"),
        (args.npm_tarball, "npm"),
    ):
        if value is not None:
            scopes.append(kind)
            violations.extend(check_artifact(value, kind))
    violations.sort()
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "scopes": scopes,
                    "status": "failed" if violations else "passed",
                    "violations": [asdict(item) for item in violations],
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
    else:
        print(f"legacy identifier check passed: {', '.join(scopes)}")
    return 1 if violations else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LegacyIdentifierError as exc:
        print(f"legacy identifier check failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
