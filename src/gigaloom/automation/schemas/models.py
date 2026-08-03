"""Contracts for deterministic editor schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ArtifactSchemaName(str, Enum):
    """Stable machine names for supported ``.giga`` artifact families."""

    AGENT = "agent"
    WORKFLOW = "workflow"
    EVAL = "eval"
    SCHEDULE_SOURCE = "schedule-source"


@dataclass(frozen=True)
class ArtifactSchema:
    """One generated JSON Schema and its editor-facing identity."""

    name: ArtifactSchemaName
    filename: str
    project_glob: str
    schema_version: int
    document: Mapping[str, Any]
