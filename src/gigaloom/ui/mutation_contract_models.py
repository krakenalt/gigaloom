"""Typed policy inventory records for unsafe-method UI routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gigaloom.runtime.policy import PermissionAction


class MutationClass(str, Enum):
    """Semantic effect of an unsafe-method route."""

    READ_ONLY = "read_only"
    LOCAL_STATE = "local_state_mutation"
    GOVERNED_EXTERNAL_EFFECT = "governed_external_effect"
    REVIEWED_PROMOTION = "reviewed_promotion"


class EnforcementControl(str, Enum):
    """Existing component that prevents an unsafe route from bypassing policy."""

    AUTHENTICATED_PROJECTION = "authenticated_projection"
    AUTHENTICATED_LOCAL_STATE = "authenticated_local_state"
    OPTIMISTIC_LOCAL_STATE = "optimistic_local_state"
    EXPLICIT_OPERATOR_ACTION = "explicit_operator_action"
    SELECTED_PLAN_PREFLIGHT = "selected_plan_preflight"
    POLICY_ENGINE = "policy_engine"
    REVIEW_BINDING = "review_binding"
    BOOTSTRAP_AUTH = "bootstrap_auth"
    OIDC_AUTH = "oidc_auth"


class ConformanceBehavior(str, Enum):
    """Machine-readable behavior exercised by retained conformance evidence."""

    AUTHENTICATION = "authentication"
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    STALE_OR_REBOUND = "stale_or_rebound"
    REDACTION = "redaction"


@dataclass(frozen=True)
class ConformanceEvidence:
    """Retained test evidence shared by one or more route contracts."""

    id: str
    behaviors: frozenset[ConformanceBehavior]
    test_nodes: tuple[str, ...]


@dataclass(frozen=True)
class MutationRouteContract:
    """One exact unsafe-method route and its real enforcement contract."""

    method: str
    path: str
    mutation_class: MutationClass
    control: EnforcementControl
    enforcement_owner: str | None
    permission_actions: tuple[PermissionAction, ...]
    evidence_ids: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, str]:
        """Return the exact HTTP method and normalized FastAPI path."""
        return self.method, self.path
