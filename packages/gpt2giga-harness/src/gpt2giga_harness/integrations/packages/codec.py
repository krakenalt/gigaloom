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


def integration_package_to_dict(package: IntegrationPackage) -> dict[str, Any]:
    """Serialize one manifest into its strict forward-only v1 shape."""
    return {
        "schema_version": package.schema_version,
        "id": package.id,
        "version": package.version,
        "publisher": package.publisher,
        "license": package.license,
        "source_type": package.source_type.value,
        "source": package.source,
        "immutable_ref": package.immutable_ref,
        "checksum": package.checksum,
        "components": [_component_to_dict(item) for item in package.components],
        "requirements": [_requirement_to_dict(item) for item in package.requirements],
        "overlays": [_overlay_to_dict(item) for item in package.overlays],
        "compatibility": [
            _compatibility_to_dict(item) for item in package.compatibility
        ],
        "scopes": [item.value for item in package.scopes],
        "update_policy": package.update_policy.value,
        "verification_steps": list(package.verification_steps),
        "rollback_steps": list(package.rollback_steps),
        "trust_evidence": [
            _trust_evidence_to_dict(item) for item in package.trust_evidence
        ],
    }


def integration_package_from_dict(data: Mapping[str, Any]) -> IntegrationPackage:
    """Parse a manifest without accepting unknown or future fields."""
    mapping = _strict_mapping(
        data,
        allowed={
            "schema_version",
            "id",
            "version",
            "publisher",
            "license",
            "source_type",
            "source",
            "immutable_ref",
            "checksum",
            "components",
            "requirements",
            "overlays",
            "compatibility",
            "scopes",
            "update_policy",
            "verification_steps",
            "rollback_steps",
            "trust_evidence",
        },
        field_name="integration package",
    )
    if mapping.get("schema_version") != INTEGRATION_PACKAGE_SCHEMA_VERSION:
        raise ValueError("unsupported integration package schema_version")
    return IntegrationPackage(
        id=_required_text(mapping.get("id"), field_name="integration id"),
        version=_required_text(
            mapping.get("version"), field_name="integration version"
        ),
        publisher=_required_text(
            mapping.get("publisher"), field_name="integration publisher"
        ),
        license=_required_text(
            mapping.get("license"), field_name="integration license"
        ),
        source_type=_enum_value(
            IntegrationSourceType,
            mapping.get("source_type"),
            field_name="integration source_type",
        ),
        source=_required_text(mapping.get("source"), field_name="integration source"),
        immutable_ref=_required_text(
            mapping.get("immutable_ref"), field_name="integration immutable_ref"
        ),
        checksum=_required_text(
            mapping.get("checksum"), field_name="integration checksum"
        ),
        components=tuple(
            _component_from_dict(item)
            for item in _required_list(mapping.get("components"), "components")
        ),
        requirements=tuple(
            _requirement_from_dict(item)
            for item in _required_list(mapping.get("requirements"), "requirements")
        ),
        overlays=tuple(
            _overlay_from_dict(item)
            for item in _required_list(mapping.get("overlays"), "overlays")
        ),
        compatibility=tuple(
            _compatibility_from_dict(item)
            for item in _required_list(mapping.get("compatibility"), "compatibility")
        ),
        scopes=tuple(
            _enum_value(InstallationScope, item, field_name="installation scope")
            for item in _required_list(mapping.get("scopes"), "scopes")
        ),
        update_policy=_enum_value(
            IntegrationUpdatePolicy,
            mapping.get("update_policy"),
            field_name="integration update_policy",
        ),
        verification_steps=tuple(
            _required_text(item, field_name="verification step")
            for item in _required_list(
                mapping.get("verification_steps"), "verification_steps"
            )
        ),
        rollback_steps=tuple(
            _required_text(item, field_name="rollback step")
            for item in _required_list(mapping.get("rollback_steps"), "rollback_steps")
        ),
        trust_evidence=tuple(
            _trust_evidence_from_dict(item)
            for item in _required_list(mapping.get("trust_evidence"), "trust_evidence")
        ),
    )


def integration_package_semantic_hash(package: IntegrationPackage) -> str:
    """Return the deterministic hash used to bind previews and approvals."""
    payload = integration_package_to_dict(package)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def integration_trust_assessment_to_dict(
    assessment: IntegrationTrustAssessment,
) -> dict[str, Any]:
    """Project only stable content-free trust diagnostics."""
    return {
        "package_id": assessment.package_id,
        "package_version": assessment.package_version,
        "manifest_hash": assessment.manifest_hash,
        "decision": assessment.decision.value,
        "install_authorized": assessment.install_authorized,
        "diagnostics": [
            {
                "code": item.code,
                "subject_id": item.subject_id,
                "classification": item.classification.value,
            }
            for item in assessment.diagnostics
        ],
    }


def extension_target_descriptor_to_dict(
    descriptor: ExtensionTargetDescriptor,
) -> dict[str, Any]:
    """Serialize target capability evidence without loading its driver."""
    return {
        "schema_version": descriptor.schema_version,
        "id": descriptor.id,
        "revision": descriptor.revision,
        "component_types": [item.value for item in descriptor.component_types],
        "scopes": [item.value for item in descriptor.scopes],
        "capabilities": list(descriptor.capabilities),
        "trust_evidence": [
            _trust_evidence_to_dict(item) for item in descriptor.trust_evidence
        ],
    }


def extension_target_descriptor_from_dict(
    data: Mapping[str, Any],
) -> ExtensionTargetDescriptor:
    """Strictly parse one extension-target SDK descriptor."""
    mapping = _strict_mapping(
        data,
        allowed={
            "schema_version",
            "id",
            "revision",
            "component_types",
            "scopes",
            "capabilities",
            "trust_evidence",
        },
        field_name="extension target descriptor",
    )
    if mapping.get("schema_version") != EXTENSION_TARGET_SCHEMA_VERSION:
        raise ValueError("unsupported extension target schema_version")
    return ExtensionTargetDescriptor(
        id=_required_text(mapping.get("id"), field_name="extension target id"),
        revision=_required_text(
            mapping.get("revision"), field_name="extension target revision"
        ),
        component_types=tuple(
            _enum_value(
                IntegrationComponentType,
                item,
                field_name="extension target component type",
            )
            for item in _required_list(
                mapping.get("component_types"), "extension target component_types"
            )
        ),
        scopes=tuple(
            _enum_value(
                InstallationScope,
                item,
                field_name="extension target scope",
            )
            for item in _required_list(mapping.get("scopes"), "extension target scopes")
        ),
        capabilities=tuple(
            _required_text(item, field_name="extension target capability")
            for item in _required_list(
                mapping.get("capabilities"), "extension target capabilities"
            )
        ),
        trust_evidence=tuple(
            _trust_evidence_from_dict(item)
            for item in _required_list(
                mapping.get("trust_evidence"), "extension target trust_evidence"
            )
        ),
    )


def _component_to_dict(item: IntegrationComponent) -> dict[str, Any]:
    return {"id": item.id, "type": item.type.value, "portable": item.portable}


def _component_from_dict(value: Any) -> IntegrationComponent:
    mapping = _strict_mapping(
        value,
        allowed={"id", "type", "portable"},
        field_name="integration component",
    )
    portable = mapping.get("portable")
    if not isinstance(portable, bool):
        raise ValueError("component portable must be a boolean")
    return IntegrationComponent(
        id=_required_text(mapping.get("id"), field_name="component id"),
        type=_enum_value(
            IntegrationComponentType,
            mapping.get("type"),
            field_name="component type",
        ),
        portable=portable,
    )


def _requirement_to_dict(item: IntegrationRequirement) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.type.value,
        "classification": item.classification.value,
        "reason": item.reason,
        "argv": list(item.argv),
        "locator": item.locator,
        "checksum": item.checksum,
        "secret_owner": item.secret_owner,
        "environment": list(item.environment),
    }


def _requirement_from_dict(value: Any) -> IntegrationRequirement:
    mapping = _strict_mapping(
        value,
        allowed={
            "id",
            "type",
            "classification",
            "reason",
            "argv",
            "locator",
            "checksum",
            "secret_owner",
            "environment",
        },
        field_name="integration requirement",
    )
    return IntegrationRequirement(
        id=_required_text(mapping.get("id"), field_name="requirement id"),
        type=_enum_value(
            IntegrationRequirementType,
            mapping.get("type"),
            field_name="requirement type",
        ),
        classification=_enum_value(
            IntegrationPolicyClass,
            mapping.get("classification"),
            field_name="requirement classification",
        ),
        reason=_required_text(mapping.get("reason"), field_name="requirement reason"),
        argv=tuple(
            _required_text(item, field_name="requirement argv")
            for item in _required_list(mapping.get("argv"), "requirement argv")
        ),
        locator=_optional_text(
            mapping.get("locator"), field_name="requirement locator"
        ),
        checksum=_optional_text(
            mapping.get("checksum"), field_name="requirement checksum"
        ),
        secret_owner=_optional_text(
            mapping.get("secret_owner"), field_name="secret owner"
        ),
        environment=tuple(
            _required_text(item, field_name="environment name")
            for item in _required_list(
                mapping.get("environment"), "requirement environment"
            )
        ),
    )


def _overlay_to_dict(item: IntegrationTargetOverlay) -> dict[str, Any]:
    return {
        "target_id": item.target_id,
        "component_ids": list(item.component_ids),
        "requirement_ids": list(item.requirement_ids),
    }


def _overlay_from_dict(value: Any) -> IntegrationTargetOverlay:
    mapping = _strict_mapping(
        value,
        allowed={"target_id", "component_ids", "requirement_ids"},
        field_name="integration overlay",
    )
    return IntegrationTargetOverlay(
        target_id=_required_text(mapping.get("target_id"), field_name="target id"),
        component_ids=tuple(
            _required_text(item, field_name="overlay component id")
            for item in _required_list(
                mapping.get("component_ids"), "overlay component_ids"
            )
        ),
        requirement_ids=tuple(
            _required_text(item, field_name="overlay requirement id")
            for item in _required_list(
                mapping.get("requirement_ids"), "overlay requirement_ids"
            )
        ),
    )


def _compatibility_to_dict(item: IntegrationCompatibility) -> dict[str, Any]:
    return {
        "target_id": item.target_id,
        "minimum_version": item.minimum_version,
        "maximum_version_exclusive": item.maximum_version_exclusive,
        "required_capabilities": list(item.required_capabilities),
    }


def _compatibility_from_dict(value: Any) -> IntegrationCompatibility:
    mapping = _strict_mapping(
        value,
        allowed={
            "target_id",
            "minimum_version",
            "maximum_version_exclusive",
            "required_capabilities",
        },
        field_name="integration compatibility",
    )
    return IntegrationCompatibility(
        target_id=_required_text(mapping.get("target_id"), field_name="target id"),
        minimum_version=_optional_text(
            mapping.get("minimum_version"), field_name="minimum version"
        ),
        maximum_version_exclusive=_optional_text(
            mapping.get("maximum_version_exclusive"),
            field_name="maximum version exclusive",
        ),
        required_capabilities=tuple(
            _required_text(item, field_name="required capability")
            for item in _required_list(
                mapping.get("required_capabilities"), "required_capabilities"
            )
        ),
    )


def _trust_evidence_to_dict(item: IntegrationTrustEvidence) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "status": item.status.value,
        "authority": item.authority,
        "revision": item.revision,
    }


def _trust_evidence_from_dict(value: Any) -> IntegrationTrustEvidence:
    mapping = _strict_mapping(
        value,
        allowed={"id", "kind", "status", "authority", "revision"},
        field_name="trust evidence",
    )
    return IntegrationTrustEvidence(
        id=_required_text(mapping.get("id"), field_name="trust evidence id"),
        kind=_enum_value(
            IntegrationTrustKind,
            mapping.get("kind"),
            field_name="trust evidence kind",
        ),
        status=_enum_value(
            IntegrationTrustStatus,
            mapping.get("status"),
            field_name="trust evidence status",
        ),
        authority=_required_text(
            mapping.get("authority"), field_name="trust evidence authority"
        ),
        revision=_required_text(
            mapping.get("revision"), field_name="trust evidence revision"
        ),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
