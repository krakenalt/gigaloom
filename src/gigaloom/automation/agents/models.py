"""Models for the agents subcontext."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentBudgets:
    """Optional execution limits captured by a reusable profile."""

    timeout_seconds: int | None = None
    max_tokens: int | None = None
    max_attempts: int = 1
    max_concurrency: int = 1


class AgentOptionStatus(str, Enum):
    """Explain whether one requested profile option reaches execution."""

    EFFECTIVE = "effective"
    DELEGATED = "delegated"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AgentOptionResolution:
    """Requested and effective value plus its enforcement boundary."""

    status: AgentOptionStatus
    requested: Any
    effective: Any
    enforcement_source: str
    detail: str


@dataclass(frozen=True)
class AgentExecutionPlan:
    """Redaction-safe operational interpretation of one AgentProfile."""

    schema_version: int
    harness_id: str
    invocation_mode: str
    options: Mapping[str, AgentOptionResolution]
    adapter_options: Mapping[str, Any]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    binary_version: str | None = None
    capability_evidence: str | None = None

    @property
    def queueable(self) -> bool:
        """Return whether the profile may be submitted without silent drift."""
        return not self.errors


@dataclass(frozen=True)
class AgentProfile:
    """Validated reusable role over an existing harness."""

    id: str
    title: str
    description: str
    schema_version: int
    harness_id: str
    instructions: str
    model: str | None = None
    reasoning_effort: str | None = None
    api_mode: str = "v2"
    invocation_mode: str = "headless"
    mode: str = "plan"
    workspace_policy: str = "auto"
    permission_profile: str = "interactive"
    prompt_files: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    memory_selectors: tuple[str, ...] = ()
    context_selectors: tuple[str, ...] = ()
    tool_ids: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    budgets: AgentBudgets = field(default_factory=AgentBudgets)
    expected_artifact: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    source_hash: str | None = None


@dataclass(frozen=True)
class AgentProfileLoadError:
    """One invalid project profile discovered without hiding valid profiles."""

    path: str
    error: str
