# ruff: noqa: E402, F401, F403, F405
"""Provider-neutral integration package and target discovery contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
from importlib.metadata import entry_points
import json
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from gpt2giga_harness.registries import (
    EntryPointFamily,
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gpt2giga_harness.types import redact_secrets


INTEGRATION_PACKAGE_SCHEMA_VERSION = 1
EXTENSION_TARGET_SCHEMA_VERSION = 1
NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP = "agent_workbench.extension_targets.v1"
EXTENSION_TARGET_ENTRY_POINTS = EntryPointFamily(
    registry_id="extension_target",
    api_version=1,
    primary_group=NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP,
)
MAX_TARGET_DISCOVERY_ERRORS = 20
MAX_TARGET_DISCOVERY_ERROR_CHARS = 400
MAX_TRUST_DIAGNOSTICS = 100
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CHECKSUM_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
from .models import *  # noqa: F403
from .validation import *  # noqa: F403
from .codec import integration_package_semantic_hash


def assess_integration_package(
    package: IntegrationPackage,
) -> IntegrationTrustAssessment:
    """Return bounded policy gates without granting installation authority."""
    diagnostics: list[IntegrationTrustDiagnostic] = []
    for requirement in package.requirements:
        decision = _policy_decision(requirement.classification)
        diagnostics.append(
            IntegrationTrustDiagnostic(
                code=f"requirement.{requirement.type.value}.{decision.value}",
                subject_id=requirement.id,
                classification=decision,
            )
        )
    for kind in IntegrationTrustKind:
        evidence_items = tuple(
            item for item in package.trust_evidence if item.kind is kind
        )
        if not evidence_items:
            diagnostics.append(
                IntegrationTrustDiagnostic(
                    code=f"trust.{kind.value}.missing",
                    subject_id=package.id,
                    classification=IntegrationTrustDecision.REVIEW_REQUIRED,
                )
            )
            continue
        for evidence in evidence_items:
            if evidence.status is IntegrationTrustStatus.VERIFIED:
                continue
            decision = (
                IntegrationTrustDecision.BLOCKED
                if evidence.status is IntegrationTrustStatus.BLOCKED
                else IntegrationTrustDecision.REVIEW_REQUIRED
            )
            diagnostics.append(
                IntegrationTrustDiagnostic(
                    code=f"trust.{kind.value}.{evidence.status.value}",
                    subject_id=evidence.id,
                    classification=decision,
                )
            )
    if InstallationScope.USER_HOME in package.scopes:
        diagnostics.append(
            IntegrationTrustDiagnostic(
                code="scope.user_home.explicit_approval",
                subject_id=package.id,
                classification=IntegrationTrustDecision.EXPLICIT_APPROVAL,
            )
        )
    if not diagnostics:
        diagnostics.append(
            IntegrationTrustDiagnostic(
                code="manifest.review_required",
                subject_id=package.id,
                classification=IntegrationTrustDecision.REVIEW_REQUIRED,
            )
        )
    normalized = tuple(
        sorted(
            diagnostics,
            key=lambda item: (item.classification.value, item.code, item.subject_id),
        )[:MAX_TRUST_DIAGNOSTICS]
    )
    decision = max(
        (item.classification for item in normalized),
        key=_decision_rank,
    )
    return IntegrationTrustAssessment(
        package_id=package.id,
        package_version=package.version,
        manifest_hash=integration_package_semantic_hash(package),
        decision=decision,
        install_authorized=False,
        diagnostics=normalized,
    )


def _policy_decision(value: IntegrationPolicyClass) -> IntegrationTrustDecision:
    return {
        IntegrationPolicyClass.REVIEW_REQUIRED: IntegrationTrustDecision.REVIEW_REQUIRED,
        IntegrationPolicyClass.EXPLICIT_APPROVAL: IntegrationTrustDecision.EXPLICIT_APPROVAL,
        IntegrationPolicyClass.PROVIDER_HANDOFF: IntegrationTrustDecision.PROVIDER_HANDOFF,
        IntegrationPolicyClass.FORBIDDEN: IntegrationTrustDecision.BLOCKED,
    }[value]


def _decision_rank(value: IntegrationTrustDecision) -> int:
    return {
        IntegrationTrustDecision.REVIEW_REQUIRED: 0,
        IntegrationTrustDecision.EXPLICIT_APPROVAL: 1,
        IntegrationTrustDecision.PROVIDER_HANDOFF: 2,
        IntegrationTrustDecision.BLOCKED: 3,
    }[value]


__all__ = [name for name in globals() if not name.startswith("__")]
