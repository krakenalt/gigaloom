"""Compatibility facade for durable runtime coordination repositories."""

from __future__ import annotations

from pathlib import Path

from gpt2giga_harness.runtime.approvals import (
    ApprovalDecisionsRepository,
    ApprovalsRepository,
)
from gpt2giga_harness.runtime.db import DbProvider, apply_migrations
from gpt2giga_harness.runtime.db.connection import (
    SQLITE_TIMEOUT_SECONDS as SQLITE_TIMEOUT_SECONDS,
)
from gpt2giga_harness.runtime.db.schema import (
    MIGRATIONS,
    RUNTIME_DB_NAME,
    RUNTIME_SCHEMA_VERSION as RUNTIME_SCHEMA_VERSION,
)
from gpt2giga_harness.runtime.jobs import (
    AttemptsRepository,
    JobClaimsRepository,
    JobsRepository,
)
from gpt2giga_harness.runtime.native import NativeProcessRepository
from gpt2giga_harness.runtime.outbox import OutboxRepository
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.diagnostics import (
    RuntimeDiagnosticsRepository,
)
from gpt2giga_harness.runtime.repositories.errors import (
    AttemptNotFoundError as AttemptNotFoundError,
    ConcurrentUpdateError as ConcurrentUpdateError,
    IdempotencyConflictError as IdempotencyConflictError,
    InvalidStateTransitionError as InvalidStateTransitionError,
    JobNotFoundError as JobNotFoundError,
    NativeProcessRecordNotFoundError as NativeProcessRecordNotFoundError,
    RuntimeStoreError as RuntimeStoreError,
    SideEffectBlockedError as SideEffectBlockedError,
    SideEffectConflictError as SideEffectConflictError,
    SideEffectNotFoundError as SideEffectNotFoundError,
)
from gpt2giga_harness.runtime.revisions import RevisionsRepository
from gpt2giga_harness.runtime.side_effects import SideEffectsRepository
from gpt2giga_harness.runtime.workers import WorkersRepository

_MIGRATIONS = MIGRATIONS

for _error_type in (
    RuntimeStoreError,
    JobNotFoundError,
    AttemptNotFoundError,
    NativeProcessRecordNotFoundError,
    IdempotencyConflictError,
    SideEffectConflictError,
    SideEffectBlockedError,
    SideEffectNotFoundError,
    ConcurrentUpdateError,
    InvalidStateTransitionError,
):
    _error_type.__module__ = __name__
del _error_type


class RuntimeCoordinationStore(
    JobsRepository,
    AttemptsRepository,
    JobClaimsRepository,
    WorkersRepository,
    ApprovalsRepository,
    ApprovalDecisionsRepository,
    SideEffectsRepository,
    NativeProcessRepository,
    OutboxRepository,
    RevisionsRepository,
    RuntimeDiagnosticsRepository,
):
    """Compatibility facade for atomic runtime coordination operations."""

    def __init__(
        self, data_dir: str | Path, *, filename: str = RUNTIME_DB_NAME
    ) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.path = self.data_dir / filename
        self.data_dir.mkdir(parents=True, exist_ok=True)
        RuntimeRepository.__init__(self, DbProvider(self.path))
        self._migrate()

    def __enter__(self) -> RuntimeCoordinationStore:
        """Return this store as a managed resource."""
        return self

    def _migrate(self) -> None:
        apply_migrations(self._db)
