"""Bounded Git-aware lexical Impact Radar for Python projects."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

from .impact_models import (
    ImpactUncertainty,
    ImpactUncertaintyKind,
)


DEFAULT_MAX_PYTHON_FILES = 5_000
DEFAULT_MAX_PYTHON_FILE_BYTES = 1_000_000
_GIT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class _ImportReference:
    module: str
    symbol: str | None
    local_name: str
    referenced: bool


@dataclass(frozen=True)
class _PythonFileFact:
    relative_path: str
    module: str
    package_boundary: str | None
    source_digest: str
    imports: tuple[_ImportReference, ...]
    public_symbols: tuple[str, ...]
    contract_markers: tuple[str, ...]
    owners: tuple[str, ...]

    def digest_payload(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "module": self.module,
            "package_boundary": self.package_boundary,
            "source_digest": self.source_digest,
            "imports": [
                {
                    "module": item.module,
                    "symbol": item.symbol,
                    "local_name": item.local_name,
                    "referenced": item.referenced,
                }
                for item in self.imports
            ],
            "public_symbols": list(self.public_symbols),
            "contract_markers": list(self.contract_markers),
            "owners": list(self.owners),
        }


@dataclass(frozen=True)
class PythonImpactIndex:
    """Immutable lexical index used for cheap changeset projections."""

    _project_root: str
    source_revision: str
    index_digest: str
    _files: tuple[_PythonFileFact, ...]
    uncertainties: tuple[ImpactUncertainty, ...]

    @property
    def scanned_file_count(self) -> int:
        """Return the number of readable Python sources in the index."""
        return len(self._files)


def compile_python_impact_index(
    project_root: str | Path,
    *,
    max_files: int = DEFAULT_MAX_PYTHON_FILES,
    max_file_bytes: int = DEFAULT_MAX_PYTHON_FILE_BYTES,
) -> PythonImpactIndex:
    """Compile a bounded index from Git-visible Python sources."""
    if max_files < 1:
        raise ValueError("max_files must be positive")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    root = Path(project_root).expanduser().resolve()
    git_root = Path(_required_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise ValueError("project_root must be the exact Git worktree root")
    source_revision = _required_git(root, "rev-parse", "HEAD")
    relative_paths = _git_python_paths(root)
    truncated = len(relative_paths) > max_files
    selected_paths = relative_paths[:max_files]
    ownership = _load_codeowners(root)
    facts: list[_PythonFileFact] = []
    uncertainties: list[ImpactUncertainty] = []

    if truncated:
        uncertainties.append(
            ImpactUncertainty(
                kind=ImpactUncertaintyKind.SCAN_TRUNCATED,
                relative_path=None,
                reason=f"git_visible_python_files_exceed_{max_files}",
            )
        )
    for relative_path in selected_paths:
        fact, file_uncertainties = _inspect_python_file(
            root,
            relative_path,
            ownership=ownership,
            max_file_bytes=max_file_bytes,
        )
        uncertainties.extend(file_uncertainties)
        if fact is not None:
            facts.append(fact)

    normalized_facts = tuple(sorted(facts, key=lambda item: item.relative_path))
    normalized_uncertainties = _sort_uncertainties(uncertainties)
    digest = _index_digest(
        source_revision,
        normalized_facts,
        normalized_uncertainties,
    )
    return PythonImpactIndex(
        _project_root=str(root),
        source_revision=source_revision,
        index_digest=digest,
        _files=normalized_facts,
        uncertainties=normalized_uncertainties,
    )


def _inspect_python_file(
    root: Path,
    relative_path: str,
    *,
    ownership: tuple[tuple[str, tuple[str, ...]], ...],
    max_file_bytes: int,
) -> tuple[_PythonFileFact | None, tuple[ImpactUncertainty, ...]]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None, (
            ImpactUncertainty(
                kind=ImpactUncertaintyKind.SOURCE_UNAVAILABLE,
                relative_path=relative_path,
                reason="git_visible_source_unavailable",
            ),
        )
    try:
        size = path.stat().st_size
        if size > max_file_bytes:
            return None, (
                ImpactUncertainty(
                    kind=ImpactUncertaintyKind.FILE_TOO_LARGE,
                    relative_path=relative_path,
                    reason=f"python_source_exceeds_{max_file_bytes}_bytes",
                ),
            )
        source_bytes = path.read_bytes()
        source = source_bytes.decode("utf-8")
        tree = ast.parse(source, filename=relative_path)
    except (OSError, UnicodeError):
        return None, (
            ImpactUncertainty(
                kind=ImpactUncertaintyKind.SOURCE_UNAVAILABLE,
                relative_path=relative_path,
                reason="python_source_unreadable",
            ),
        )
    except SyntaxError:
        return None, (
            ImpactUncertainty(
                kind=ImpactUncertaintyKind.SYNTAX_ERROR,
                relative_path=relative_path,
                reason="python_source_does_not_parse",
            ),
        )

    module = _module_name(root, relative_path)
    imports = _imports_for_tree(tree, module, relative_path.endswith("/__init__.py"))
    uncertainties = _lexical_uncertainties(tree, relative_path)
    return (
        _PythonFileFact(
            relative_path=relative_path,
            module=module,
            package_boundary=_package_boundary(root, relative_path),
            source_digest=hashlib.sha256(source_bytes).hexdigest(),
            imports=imports,
            public_symbols=_public_symbols(tree),
            contract_markers=_contract_markers(tree, relative_path),
            owners=_owners_for_path(relative_path, ownership),
        ),
        uncertainties,
    )


def _imports_for_tree(
    tree: ast.AST,
    current_module: str,
    is_package: bool,
) -> tuple[_ImportReference, ...]:
    used_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    references: list[_ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(
                _ImportReference(
                    module=alias.name,
                    symbol=None,
                    local_name=alias.asname or alias.name.split(".", 1)[0],
                    referenced=(alias.asname or alias.name.split(".", 1)[0])
                    in used_names,
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(
                current_module,
                is_package=is_package,
                level=node.level,
                imported_module=node.module,
            )
            references.extend(
                _ImportReference(
                    module=module,
                    symbol=alias.name,
                    local_name=alias.asname or alias.name,
                    referenced=(alias.asname or alias.name) in used_names,
                )
                for alias in node.names
                if alias.name != "*"
            )
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.module,
                item.symbol or "",
                item.local_name,
                item.referenced,
            ),
        )
    )


def _resolve_import_from(
    current_module: str,
    *,
    is_package: bool,
    level: int,
    imported_module: str | None,
) -> str:
    if level == 0:
        return imported_module or ""
    package_parts = current_module.split(".")
    if not is_package:
        package_parts = package_parts[:-1]
    keep = max(0, len(package_parts) - (level - 1))
    parts = package_parts[:keep]
    if imported_module:
        parts.extend(imported_module.split("."))
    return ".".join(parts)


def _public_symbols(tree: ast.Module) -> tuple[str, ...]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return tuple(sorted(names))


def _contract_markers(tree: ast.Module, relative_path: str) -> tuple[str, ...]:
    path = PurePosixPath(relative_path)
    markers: set[str] = set()
    if path.name == "__init__.py":
        markers.add("package_facade")
    if path.name == "api.py":
        markers.add("api_module")
    if "contract" in path.stem or "contracts" in path.parts:
        markers.add("contract_module")
    if any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in (
                node.targets if isinstance(node, ast.Assign) else (node.target,)
            )
        )
        for node in tree.body
    ):
        markers.add("declares___all__")
    return tuple(sorted(markers))


def _lexical_uncertainties(
    tree: ast.AST,
    relative_path: str,
) -> tuple[ImpactUncertainty, ...]:
    kinds: set[ImpactUncertaintyKind] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name in {"__import__", "importlib.import_module"}:
            kinds.add(ImpactUncertaintyKind.DYNAMIC_IMPORT)
        if name in {
            "getattr",
            "setattr",
            "globals",
            "locals",
            "importlib.metadata.entry_points",
            "pkg_resources.iter_entry_points",
        }:
            kinds.add(ImpactUncertaintyKind.RUNTIME_WIRING)
    return tuple(
        ImpactUncertainty(
            kind=kind,
            relative_path=relative_path,
            reason="lexical_analysis_cannot_resolve_runtime_target",
        )
        for kind in sorted(kinds, key=lambda item: item.value)
    )


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _module_name(root: Path, relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    parts = list(path.with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts.pop()
    directory_parts = list(PurePosixPath(relative_path).parent.parts)
    package_start: int | None = None
    for index in range(len(directory_parts)):
        candidate = root.joinpath(*directory_parts[: index + 1], "__init__.py")
        if candidate.is_file():
            package_start = index
            break
    if package_start is not None:
        parts = parts[package_start:]
    elif parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def _package_boundary(root: Path, relative_path: str) -> str | None:
    directory_parts = list(PurePosixPath(relative_path).parent.parts)
    package_parts: list[str] = []
    started = False
    for index, part in enumerate(directory_parts):
        is_package = root.joinpath(
            *directory_parts[: index + 1], "__init__.py"
        ).is_file()
        if is_package:
            started = True
            package_parts.append(part)
        elif started:
            break
    return ".".join(package_parts) or None


def _git_python_paths(root: Path) -> tuple[str, ...]:
    output = _required_git_bytes(
        root,
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
        "--",
        "*.py",
    )
    paths = {item.decode("utf-8") for item in output.split(b"\0") if item}
    return tuple(sorted(paths))


def _required_git(root: Path, *args: str) -> str:
    output = _required_git_bytes(root, *args)
    value = output.decode("utf-8").strip()
    if not value:
        raise ValueError(f"git {' '.join(args)} returned no value")
    return value


def _required_git_bytes(root: Path, *args: str) -> bytes:
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=root,
            check=False,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git {' '.join(args)} failed") from exc
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed")
    return result.stdout


def _load_codeowners(
    root: Path,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    for relative_path in (
        ".github/CODEOWNERS",
        "CODEOWNERS",
        "docs/CODEOWNERS",
    ):
        path = root / relative_path
        if not path.is_file():
            continue
        rules: list[tuple[str, tuple[str, ...]]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return ()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) >= 2:
                rules.append((fields[0], tuple(fields[1:])))
        return tuple(rules)
    return ()


def _owners_for_path(
    relative_path: str,
    rules: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[str, ...]:
    owners: tuple[str, ...] = ()
    for pattern, candidate_owners in rules:
        if _codeowners_match(relative_path, pattern):
            owners = candidate_owners
    return owners


def _codeowners_match(relative_path: str, pattern: str) -> bool:
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):
        return relative_path.startswith(normalized)
    if "/" not in normalized:
        return fnmatch.fnmatch(PurePosixPath(relative_path).name, normalized)
    return fnmatch.fnmatch(relative_path, normalized)


def _sort_uncertainties(
    uncertainties: Iterable[ImpactUncertainty],
) -> tuple[ImpactUncertainty, ...]:
    return tuple(
        sorted(
            set(uncertainties),
            key=lambda item: (
                item.relative_path or "",
                item.kind.value,
                item.reason,
            ),
        )
    )


def _index_digest(
    source_revision: str,
    facts: tuple[_PythonFileFact, ...],
    uncertainties: tuple[ImpactUncertainty, ...],
) -> str:
    payload = {
        "source_revision": source_revision,
        "files": [item.digest_payload() for item in facts],
        "uncertainties": [item.to_dict() for item in uncertainties],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_MAX_PYTHON_FILE_BYTES",
    "DEFAULT_MAX_PYTHON_FILES",
    "PythonImpactIndex",
    "compile_python_impact_index",
]
