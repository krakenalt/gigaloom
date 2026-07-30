"""Models for the evals subcontext."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from gigaloom.types import GigaChatApiMode, HarnessCapability


class EvalSpecNotFoundError(FileNotFoundError):
    """Raised when a project eval spec is missing."""


class EvalRunNotFoundError(KeyError):
    """Raised when an eval run cannot be found."""


@dataclass(frozen=True)
class EvalCheckSpec:
    """One deterministic check in an eval case."""

    type: str
    value: str
    name: str | None = None
    case_sensitive: bool = True


@dataclass(frozen=True)
class EvalCaseSpec:
    """One prompt case from an eval spec."""

    id: str
    prompt: str
    harnesses: tuple[str, ...] = ()
    checks: tuple[EvalCheckSpec, ...] = ()
    required_capability: HarnessCapability | None = None


@dataclass(frozen=True)
class HarnessEvalSpec:
    """Parsed `.giga/evals/*.yaml` spec."""

    name: str
    path: str
    description: str | None = None
    harnesses: tuple[str, ...] = ()
    model: str | None = None
    api_mode: GigaChatApiMode = GigaChatApiMode.V2
    mode: str = "plan"
    workspace_policy: str = "current"
    cases: tuple[EvalCaseSpec, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalSpecLoadError:
    """Safe parse error for a project eval spec."""

    path: str
    message: str


@dataclass(frozen=True)
class EvalCheckResult:
    """One evaluated check result."""

    type: str
    value: str
    passed: bool
    message: str
    name: str | None = None


@dataclass(frozen=True)
class EvalCaseRunResult:
    """Result for one case/harness pair."""

    case_id: str
    harness_id: str
    status: str
    ok: bool
    score: float
    checks: tuple[EvalCheckResult, ...] = ()
    session_id: str | None = None
    run_id: str | None = None
    output_text: str | None = None
    error: str | None = None
    repetition: int = 1
    target_type: str = "harness"
    target_id: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessEvalRun:
    """Persisted eval run scorecard."""

    id: str
    spec_name: str
    spec_path: str
    project_id: str
    project_root: str
    project_name: str
    session_id: str
    status: str
    model: str | None
    api_mode: GigaChatApiMode
    mode: str
    workspace_policy: str
    harness_ids: tuple[str, ...]
    created_at: str
    updated_at: str
    results: tuple[EvalCaseRunResult, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
