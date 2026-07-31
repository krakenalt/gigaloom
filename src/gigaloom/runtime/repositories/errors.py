"""Stable errors raised by runtime coordination repositories."""


class RuntimeStoreError(RuntimeError):
    """Base error for durable coordination operations."""


class JobNotFoundError(RuntimeStoreError):
    """Raised when a logical job does not exist."""


class AttemptNotFoundError(RuntimeStoreError):
    """Raised when a job attempt does not exist."""


class NativeProcessRecordNotFoundError(RuntimeStoreError):
    """Raised when a durable native process record does not exist."""


class IdempotencyConflictError(RuntimeStoreError):
    """Raised when one idempotency key is reused for a different job."""


class SideEffectConflictError(RuntimeStoreError):
    """Raised when a side-effect token or completion is rebound."""


class SideEffectBlockedError(RuntimeStoreError):
    """Raised when an incomplete side effect cannot be safely replayed."""


class SideEffectNotFoundError(RuntimeStoreError):
    """Raised when a durable side-effect record does not exist."""


class ConcurrentUpdateError(RuntimeStoreError):
    """Raised when an expected coordination state changed concurrently."""


class InvalidStateTransitionError(RuntimeStoreError):
    """Raised when a terminal object is restarted without an explicit retry."""
