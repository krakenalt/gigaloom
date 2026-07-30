"""Public advisory projection for the Python Impact Radar index."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Iterable

from .impact_index import (
    DEFAULT_MAX_PYTHON_FILE_BYTES,
    DEFAULT_MAX_PYTHON_FILES,
    PythonImpactIndex,
    _PythonFileFact,
    _module_name,
    _sort_uncertainties,
    compile_python_impact_index,
)
from .impact_models import (
    ImpactedPythonFile,
    ImpactUncertainty,
    ImpactUncertaintyKind,
    PublicContractMarker,
    PythonImpactResult,
)


def project_python_impact(
    index: PythonImpactIndex,
    changed_paths: Iterable[str | PurePosixPath],
) -> PythonImpactResult:
    """Project a changed Python set through an existing lexical index."""
    normalized_changes = tuple(
        sorted({_normalize_relative_python_path(path) for path in changed_paths})
    )
    if not normalized_changes:
        raise ValueError("changed_paths must contain at least one Python path")
    root = Path(index._project_root)
    by_path = {fact.relative_path: fact for fact in index._files}
    changed_modules = {
        path: (by_path[path].module if path in by_path else _module_name(root, path))
        for path in normalized_changes
    }
    changed_module_names = set(changed_modules.values())
    changed_files = tuple(
        _impacted_file(by_path[path], ("changed",))
        for path in normalized_changes
        if path in by_path
    )
    affected: list[ImpactedPythonFile] = []
    nearest_tests: set[str] = set()

    for fact in index._files:
        if fact.relative_path in normalized_changes:
            continue
        reasons = _impact_reasons(fact, changed_module_names)
        if reasons:
            affected.append(_impacted_file(fact, reasons))
        if _is_test_path(fact.relative_path) and (
            reasons or _is_stem_neighbor(fact.relative_path, normalized_changes)
        ):
            nearest_tests.add(fact.relative_path)

    public_contracts = tuple(
        PublicContractMarker(
            relative_path=path,
            markers=fact.contract_markers,
            public_symbols=fact.public_symbols,
        )
        for path in normalized_changes
        if (fact := by_path.get(path)) is not None
        and (fact.contract_markers or fact.public_symbols)
    )
    uncertainties = list(index.uncertainties)
    for path in normalized_changes:
        if path not in by_path:
            uncertainties.append(
                ImpactUncertainty(
                    kind=ImpactUncertaintyKind.SOURCE_UNAVAILABLE,
                    relative_path=path,
                    reason="changed_source_not_present_in_index",
                )
            )
    boundaries = {
        item.package_boundary
        for item in (*changed_files, *affected)
        if item.package_boundary is not None
    }
    return PythonImpactResult(
        source_revision=index.source_revision,
        index_digest=index.index_digest,
        changed_paths=normalized_changes,
        changed_files=changed_files,
        affected_files=tuple(sorted(affected, key=lambda item: item.relative_path)),
        nearest_tests=tuple(sorted(nearest_tests)),
        package_boundaries=tuple(sorted(boundaries)),
        public_contracts=public_contracts,
        uncertainties=_sort_uncertainties(uncertainties),
        scanned_file_count=index.scanned_file_count,
    )


def analyze_python_impact(
    project_root: str | Path,
    changed_paths: Iterable[str | PurePosixPath],
    *,
    max_files: int = DEFAULT_MAX_PYTHON_FILES,
    max_file_bytes: int = DEFAULT_MAX_PYTHON_FILE_BYTES,
) -> PythonImpactResult:
    """Compile and project in one call for non-cached consumers."""
    return project_python_impact(
        compile_python_impact_index(
            project_root,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        ),
        changed_paths,
    )


def _impact_reasons(
    fact: _PythonFileFact,
    changed_modules: set[str],
) -> tuple[str, ...]:
    reasons: set[str] = set()
    for reference in fact.imports:
        target = (
            f"{reference.module}.{reference.symbol}"
            if reference.symbol is not None
            else reference.module
        )
        for module in sorted(changed_modules):
            if (
                target != module
                and reference.module != module
                and not target.startswith(f"{module}.")
            ):
                continue
            reasons.add(f"import:{module}")
            if reference.symbol is not None and reference.referenced:
                reasons.add(f"symbol_reference:{target}")
    return tuple(sorted(reasons))


def _impacted_file(
    fact: _PythonFileFact,
    reasons: tuple[str, ...],
) -> ImpactedPythonFile:
    return ImpactedPythonFile(
        relative_path=fact.relative_path,
        module=fact.module,
        package_boundary=fact.package_boundary,
        reasons=reasons,
        owners=fact.owners,
    )


def _is_test_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    return path.name.startswith("test_") or "tests" in path.parts


def _is_stem_neighbor(
    candidate: str,
    changed_paths: tuple[str, ...],
) -> bool:
    candidate_stem = PurePosixPath(candidate).stem.removeprefix("test_")
    return any(
        candidate_stem == PurePosixPath(path).stem.removeprefix("test_")
        for path in changed_paths
    )


def _normalize_relative_python_path(path: str | PurePosixPath) -> str:
    normalized = PurePosixPath(str(path))
    if (
        not str(normalized)
        or normalized.is_absolute()
        or ".." in normalized.parts
        or normalized.suffix != ".py"
    ):
        raise ValueError("changed paths must be relative Python files")
    return normalized.as_posix()


__all__ = [
    "DEFAULT_MAX_PYTHON_FILE_BYTES",
    "DEFAULT_MAX_PYTHON_FILES",
    "ImpactUncertainty",
    "ImpactUncertaintyKind",
    "ImpactedPythonFile",
    "PublicContractMarker",
    "PythonImpactIndex",
    "PythonImpactResult",
    "analyze_python_impact",
    "compile_python_impact_index",
    "project_python_impact",
]
