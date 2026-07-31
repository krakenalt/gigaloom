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

from gigaloom.integration_catalog import (
    CatalogConflictError,
    CatalogSourceType,
)
from gigaloom.integration_flows import (
    BUILTIN_FLOW_TARGETS,
    IntegrationFlowRecord,
    IntegrationFlowService,
    IntegrationFlowStatus,
)
from gigaloom.integration_groups import (
    GroupedIntegrationService,
    IntegrationGroupStatus,
)
from gigaloom.integration_packages import IntegrationComponentType
from gigaloom.integration_runtime import IntegrationRuntimeStore
from gigaloom.diagnostics.inventory.capabilities import IntegrationLifecycle
from gigaloom.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_LIFECYCLE_SCHEMA_VERSION = 1
MAX_LIFECYCLE_OPERATIONS = 500
_OPERATION_ID_RE = re.compile(r"iop_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
from .lifecycle_models import *  # noqa: F403


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("integration lifecycle revision is invalid")
    return value


def _json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_operation_id(value: str) -> None:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("integration lifecycle operation id is invalid")


def _validate_plan_id(value: str) -> None:
    if _PLAN_ID_RE.fullmatch(value) is None:
        raise ValueError("integration lifecycle plan id is invalid")


def _validate_identity(value: str, *, field_name: str) -> None:
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


__all__ = [name for name in globals() if not name.startswith("__")]
