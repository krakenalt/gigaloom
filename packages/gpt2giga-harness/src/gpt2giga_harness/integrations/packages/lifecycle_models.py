# ruff: noqa: E402, F401, F403, F405
"""Durable product lifecycle for installed integrations and extension packs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from uuid import uuid4

from gpt2giga_harness.integration_catalog import (
    CatalogConflictError,
    CatalogSourceType,
)
from gpt2giga_harness.integration_flows import (
    BUILTIN_FLOW_TARGETS,
    IntegrationFlowRecord,
    IntegrationFlowService,
    IntegrationFlowStatus,
)
from gpt2giga_harness.integration_groups import (
    GroupedIntegrationService,
    IntegrationGroupStatus,
)
from gpt2giga_harness.integration_packages import IntegrationComponentType
from gpt2giga_harness.integration_runtime import IntegrationRuntimeStore
from gpt2giga_harness.product_capabilities import IntegrationLifecycle
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_LIFECYCLE_SCHEMA_VERSION = 1
MAX_LIFECYCLE_OPERATIONS = 500
_OPERATION_ID_RE = re.compile(r"iop_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class IntegrationLifecycleAction(str, Enum):
    """Distinct product verbs; none is an alias for another."""

    ENABLE = "enable"
    DISABLE = "disable"
    UNINSTALL = "uninstall"
    DELETE_DEFINITION = "delete_definition"


class IntegrationLifecycleOperationStatus(str, Enum):
    """Durable lifecycle operation outcomes."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    SUCCEEDED = "succeeded"
    COMPENSATED = "compensated"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class IntegrationLifecycleError(RuntimeError):
    """Base error for product lifecycle operations."""


class IntegrationLifecycleConflictError(IntegrationLifecycleError):
    """Raised when state or approval changed after preview."""


class IntegrationLifecycleNotFoundError(IntegrationLifecycleError):
    """Raised when a lifecycle operation does not exist."""


__all__ = [name for name in globals() if not name.startswith("__")]
