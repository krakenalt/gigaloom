"""In-memory credential broker used for hermetic control-plane execution."""

from __future__ import annotations

from threading import RLock

from gigaloom.runtime.credentials.models import (
    CredentialSourceProjection,
    CredentialSourceRegistration,
    project_credential_source,
)


class CredentialSourceConflictError(ValueError):
    """Raised when one source id is rebound to different metadata."""


class CredentialSourceNotFoundError(LookupError):
    """Raised when one unknown source projection is requested."""


class InMemoryCredentialBroker:
    """Fake broker that owns reference metadata but never resolves secrets."""

    def __init__(self, broker_id: str) -> None:
        if not isinstance(broker_id, str) or not broker_id:
            raise ValueError("credential broker id is invalid")
        self._broker_id = broker_id
        self._sources: dict[str, CredentialSourceRegistration] = {}
        self._lock = RLock()

    @property
    def broker_id(self) -> str:
        """Return the immutable broker identity."""
        return self._broker_id

    def register_source(
        self,
        source: CredentialSourceRegistration,
    ) -> CredentialSourceProjection:
        """Register one exact SecretRef owner and return safe metadata."""
        if not isinstance(source, CredentialSourceRegistration):
            raise ValueError("credential source registration is invalid")
        if source.broker_id != self._broker_id:
            raise ValueError("credential source broker binding mismatch")
        with self._lock:
            previous = self._sources.get(source.source_id)
            if previous is not None and previous != source:
                raise CredentialSourceConflictError(
                    "credential source registration conflicts"
                )
            self._sources[source.source_id] = source
        return project_credential_source(source)

    def source_projection(self, source_id: str) -> CredentialSourceProjection:
        """Return one content-free source projection."""
        with self._lock:
            source = self._sources.get(source_id)
        if source is None:
            raise CredentialSourceNotFoundError("credential source not found")
        return project_credential_source(source)

    def list_source_projections(self) -> tuple[CredentialSourceProjection, ...]:
        """Return stable projections ordered by source id."""
        with self._lock:
            sources = tuple(self._sources[key] for key in sorted(self._sources))
        return tuple(project_credential_source(source) for source in sources)


__all__ = [
    "CredentialSourceConflictError",
    "CredentialSourceNotFoundError",
    "InMemoryCredentialBroker",
]
