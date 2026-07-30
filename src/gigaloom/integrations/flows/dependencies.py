# ruff: noqa: E402, F401, F403, F405
"""Application-owned add-integration previews and lifecycle operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from gigaloom.builtin_skills import (
    BUILTIN_SKILL_SOURCE_ID,
    get_builtin_skill_bundle,
    import_builtin_skills,
)
from gigaloom.claude_mcp_target import (
    CLAUDE_MCP_TARGET_DESCRIPTOR,
    CLAUDE_MCP_TARGET_ID,
    ClaudeMCPRequest,
    ClaudeMCPTargetDriver,
)
from gigaloom.claude_plugin_target import (
    CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
    CLAUDE_PLUGIN_TARGET_ID,
    ClaudePluginApproval,
    ClaudePluginRequest,
    ClaudePluginSource,
    ClaudePluginSourceKind,
    ClaudePluginTargetDriver,
)
from gigaloom.codex_mcp_target import (
    CODEX_MCP_TARGET_DESCRIPTOR,
    CODEX_MCP_TARGET_ID,
    CodexMCPRequest,
    CodexMCPTargetDriver,
)
from gigaloom.external_mcp import (
    HARNESS_MANAGED_MCP_TARGET_ID,
    ExternalMCPDescriptor,
    ExternalMCPToolPolicy,
    external_mcp_selection_from_dict,
    external_mcp_server_spec,
    normalize_external_mcp_candidate,
    project_external_mcp_target,
)
from gigaloom.external_skills import ExternalSkillStore, parse_external_skill
from gigaloom.codex_plugin_target import (
    CODEX_PLUGIN_TARGET_DESCRIPTOR,
    CODEX_PLUGIN_TARGET_ID,
    CodexPluginApproval,
    CodexPluginRequest,
    CodexPluginSource,
    CodexPluginSourceKind,
    CodexPluginTargetDriver,
)
from gigaloom.gemini_extension_target import (
    GEMINI_EXTENSION_TARGET_DESCRIPTOR,
    GEMINI_EXTENSION_TARGET_ID,
    GeminiExtensionApproval,
    GeminiExtensionHandoff,
    GeminiExtensionRequest,
    GeminiExtensionSource,
    GeminiExtensionSourceKind,
    GeminiExtensionTargetDriver,
)
from gigaloom.gemini_mcp_target import (
    GEMINI_MCP_TARGET_DESCRIPTOR,
    GEMINI_MCP_TARGET_ID,
    GeminiMCPRequest,
    GeminiMCPTargetDriver,
)
from gigaloom.integration_catalog import (
    CatalogEntry,
    CatalogSourceType,
    IntegrationCatalogStore,
)
from gigaloom.integration_installer import (
    InstallationApproval,
    InstallationConflictError,
    TransactionalIntegrationInstaller,
)
from gigaloom.integration_packages import (
    ExtensionTargetDescriptor,
    InstallationScope,
    IntegrationCompatibility,
    IntegrationComponent,
    IntegrationComponentType,
    IntegrationPackage,
    IntegrationPolicyClass,
    IntegrationRequirement,
    IntegrationRequirementType,
    IntegrationSourceType,
    IntegrationTargetOverlay,
    IntegrationTrustEvidence,
    IntegrationTrustKind,
    IntegrationTrustDecision,
    IntegrationTrustStatus,
    IntegrationUpdatePolicy,
    assess_integration_package,
    extension_target_descriptor_to_dict,
    integration_package_from_dict,
    integration_package_semantic_hash,
    integration_package_to_dict,
)
from gigaloom.managed_mcp_inventory import ManagedMCPInventoryStore
from gigaloom.mcp_authoring import (
    MCPAuthoringTransport,
    mcp_authoring_configuration_from_dict,
    resolve_mcp_authoring_cwd,
)
from gigaloom.mcp import MCPTransport
from gigaloom.portable_skills import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    GEMINI_SKILL_TARGET_ID,
    SkillCapabilitySnapshot,
    build_skill_installation_request,
    discover_generated_skill,
    generate_skill_package,
    generated_skill_verifier,
    probe_skill_target,
)
from gigaloom.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_FLOW_SCHEMA_VERSION = 1
MAX_INTEGRATION_FLOWS = 500
MAX_FLOW_EVENTS = 50
MAX_CONFIGURATION_FIELDS = 64
_FLOW_ID_RE = re.compile(r"flow_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|secret|token|credential|api[_-]?key)", re.IGNORECASE
)

__all__ = [name for name in globals() if not name.startswith("__")]
