"""Public advisory contracts for Python Impact Radar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Any


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ImpactUncertaintyKind(str, Enum):
    """Static-analysis limits that prevent a complete impact claim."""

    DYNAMIC_IMPORT = "dynamic_import"
    RUNTIME_WIRING = "runtime_wiring"
    SYNTAX_ERROR = "syntax_error"
    FILE_TOO_LARGE = "file_too_large"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SCAN_TRUNCATED = "scan_truncated"


@dataclass(frozen=True)
class ImpactUncertainty:
    """Content-free uncertainty emitted by the lexical analyzer."""

    kind: ImpactUncertaintyKind
    relative_path: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.relative_path is not None:
            _validate_relative_path(self.relative_path)

    def to_dict(self) -> dict[str, str | None]:
        """Return a stable public representation."""
        return {
            "kind": self.kind.value,
            "relative_path": self.relative_path,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ImpactedPythonFile:
    """One source file lexically related to the requested change."""

    relative_path: str
    module: str
    package_boundary: str | None
    reasons: tuple[str, ...]
    owners: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable public representation."""
        return {
            "relative_path": self.relative_path,
            "module": self.module,
            "package_boundary": self.package_boundary,
            "reasons": list(self.reasons),
            "owners": list(self.owners),
        }


@dataclass(frozen=True)
class PublicContractMarker:
    """Lexical public-contract evidence in a changed Python file."""

    relative_path: str
    markers: tuple[str, ...]
    public_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable public representation."""
        return {
            "relative_path": self.relative_path,
            "markers": list(self.markers),
            "public_symbols": list(self.public_symbols),
        }


@dataclass(frozen=True)
class PythonImpactResult:
    """Bounded advisory projection with no edit or execution authority."""

    source_revision: str
    index_digest: str
    changed_paths: tuple[str, ...]
    changed_files: tuple[ImpactedPythonFile, ...]
    affected_files: tuple[ImpactedPythonFile, ...]
    nearest_tests: tuple[str, ...]
    package_boundaries: tuple[str, ...]
    public_contracts: tuple[PublicContractMarker, ...]
    uncertainties: tuple[ImpactUncertainty, ...]
    scanned_file_count: int
    advisory_only: bool = True

    def __post_init__(self) -> None:
        if not self.advisory_only:
            raise ValueError("Impact Radar cannot grant edit or execution authority")
        if not self.source_revision:
            raise ValueError("source_revision is required")
        if _SHA256_RE.fullmatch(self.index_digest) is None:
            raise ValueError("index_digest must be a lowercase SHA-256 digest")
        if self.scanned_file_count < 0:
            raise ValueError("scanned_file_count must be non-negative")
        for path in (*self.changed_paths, *self.nearest_tests):
            _validate_relative_path(path)

    def to_dict(self) -> dict[str, Any]:
        """Return the content-free advisory result."""
        return {
            "source_revision": self.source_revision,
            "index_digest": self.index_digest,
            "changed_paths": list(self.changed_paths),
            "changed_files": [item.to_dict() for item in self.changed_files],
            "affected_files": [item.to_dict() for item in self.affected_files],
            "nearest_tests": list(self.nearest_tests),
            "package_boundaries": list(self.package_boundaries),
            "public_contracts": [item.to_dict() for item in self.public_contracts],
            "uncertainties": [item.to_dict() for item in self.uncertainties],
            "scanned_file_count": self.scanned_file_count,
            "advisory_only": self.advisory_only,
        }


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("impact paths must stay within the project root")


__all__ = [
    "ImpactUncertainty",
    "ImpactUncertaintyKind",
    "ImpactedPythonFile",
    "PublicContractMarker",
    "PythonImpactResult",
]
