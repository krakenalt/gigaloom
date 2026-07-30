"""Provider health evidence, probes, and bounded checks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import time
from pathlib import Path
from typing import Callable, Protocol

from gigaloom.execution import ProviderRef
from gigaloom.sessions.contracts import exclusive_file_lock

from .profiles import ProviderProfile
from .registry import (
    MAX_PROVIDER_CHECK_TIMEOUT_SECONDS,
    PROVIDER_HEALTH_SCHEMA_VERSION,
    ProviderRegistryEntry,
)


class ProviderHealthStatus(str, Enum):
    """Stable connection-health state for a provider revision."""

    READY = "ready"
    UNHEALTHY = "unhealthy"
    BLOCKED = "blocked"


class ProviderFailureKind(str, Enum):
    """Independent failure axis for a provider connection check."""

    NETWORK_POLICY = "network_policy"
    AUTHENTICATION = "authentication"
    COMPATIBILITY = "compatibility"
    PROVIDER_HEALTH = "provider_health"
    TRANSPORT = "transport"


class ProviderDiscoveryStatus(str, Enum):
    """Truthful state of the latest model-discovery attempt."""

    NOT_REQUESTED = "not_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProviderModelSource(str, Enum):
    """Evidence source for one model visible after a check."""

    DISCOVERED = "discovered"
    CONFIGURED_FALLBACK = "configured_fallback"


@dataclass(frozen=True, order=True)
class ProviderModelEvidence:
    """One bounded model name with explicit discovery provenance."""

    model: str
    source: ProviderModelSource

    def __post_init__(self) -> None:
        _validate_model(self.model)
        if not isinstance(self.source, ProviderModelSource):
            raise ValueError("provider model source is invalid")


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    """Persistable, content-free connection and discovery evidence."""

    provider: ProviderRef
    status: ProviderHealthStatus
    checked_at: str
    duration_ms: int
    discovery_status: ProviderDiscoveryStatus
    models: tuple[ProviderModelEvidence, ...] = ()
    failure_kind: ProviderFailureKind | None = None
    reason_code: str | None = None
    discovery_reason_code: str | None = None
    cached: bool = False
    schema_version: int = PROVIDER_HEALTH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_HEALTH_SCHEMA_VERSION:
            raise ValueError("unsupported provider health schema_version")
        if not isinstance(self.provider, ProviderRef):
            raise ValueError("provider health reference is invalid")
        if not isinstance(self.status, ProviderHealthStatus):
            raise ValueError("provider health status is invalid")
        _parse_timestamp(self.checked_at)
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("provider health duration_ms must be non-negative")
        if not isinstance(self.discovery_status, ProviderDiscoveryStatus):
            raise ValueError("provider discovery status is invalid")
        models = _normalize_models(self.models)
        object.__setattr__(self, "models", models)
        if self.failure_kind is not None and not isinstance(
            self.failure_kind, ProviderFailureKind
        ):
            raise ValueError("provider health failure kind is invalid")
        if self.status is ProviderHealthStatus.READY:
            if self.failure_kind is not None or self.reason_code is not None:
                raise ValueError("ready provider health cannot retain a failure")
        elif self.failure_kind is None or self.reason_code is None:
            raise ValueError("failed provider health requires a typed reason")
        if self.status is ProviderHealthStatus.BLOCKED and (
            self.failure_kind is not ProviderFailureKind.NETWORK_POLICY
        ):
            raise ValueError("blocked provider health requires network policy denial")
        _validate_optional_reason(self.reason_code)
        _validate_optional_reason(self.discovery_reason_code)
        if self.discovery_status is ProviderDiscoveryStatus.FAILED:
            if self.discovery_reason_code is None:
                raise ValueError("failed discovery requires a reason code")
        elif self.discovery_reason_code is not None:
            raise ValueError("successful discovery cannot retain a failure reason")
        if not isinstance(self.cached, bool):
            raise ValueError("provider health cached must be a boolean")


@dataclass(frozen=True)
class ProviderProbeRequest:
    """Runtime-only connection request containing policy references, not values."""

    profile: ProviderProfile
    timeout_seconds: float
    discover_models: bool
    proxy_policy_ref: str | None
    tls_policy_ref: str | None
    egress_policy_ref: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ProviderProfile):
            raise ValueError("provider probe profile is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= MAX_PROVIDER_CHECK_TIMEOUT_SECONDS
        ):
            raise ValueError("provider probe timeout is outside the bounded range")
        if not isinstance(self.discover_models, bool):
            raise ValueError("provider probe discovery flag must be a boolean")
        expected = (
            self.profile.proxy_policy_ref,
            self.profile.tls_policy_ref,
            self.profile.egress_policy_ref,
        )
        if (
            self.proxy_policy_ref,
            self.tls_policy_ref,
            self.egress_policy_ref,
        ) != expected:
            raise ValueError("provider probe policy references changed")


@dataclass(frozen=True)
class ProviderProbeResponse:
    """Successful backend connection result with optional model discovery."""

    models: tuple[str, ...] = ()
    discovery_succeeded: bool = True
    discovery_reason_code: str | None = None

    def __post_init__(self) -> None:
        models = _normalize_discovered_model_names(self.models)
        object.__setattr__(self, "models", models)
        if not isinstance(self.discovery_succeeded, bool):
            raise ValueError("provider discovery success must be a boolean")
        _validate_optional_reason(self.discovery_reason_code)
        if self.discovery_succeeded and self.discovery_reason_code is not None:
            raise ValueError("successful discovery cannot retain a failure reason")
        if not self.discovery_succeeded and self.discovery_reason_code is None:
            raise ValueError("failed discovery requires a reason code")


@dataclass(frozen=True)
class ProviderNetworkPolicyDecision:
    """Content-free admission result evaluated before provider traffic."""

    allowed: bool
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValueError("network policy allowed must be a boolean")
        _validate_optional_reason(self.reason_code)
        if self.allowed == (self.reason_code is not None):
            raise ValueError("network policy decision reason is inconsistent")


class ProviderProbeBackend(Protocol):
    """Injected owner of provider-specific connection and discovery I/O."""

    def check(self, request: ProviderProbeRequest) -> ProviderProbeResponse:
        """Run one bounded provider connection check."""


class ProviderProbeFailure(RuntimeError):
    """Base provider check failure carrying only a stable reason code."""

    kind: ProviderFailureKind

    def __init__(self, reason_code: str) -> None:
        _validate_reason(reason_code)
        super().__init__(reason_code)
        self.reason_code = reason_code


class ProviderAuthenticationFailure(ProviderProbeFailure):
    """Provider rejected or could not resolve authentication."""

    kind = ProviderFailureKind.AUTHENTICATION


class ProviderCompatibilityFailure(ProviderProbeFailure):
    """Provider protocol or dialect is incompatible with the check backend."""

    kind = ProviderFailureKind.COMPATIBILITY


class ProviderHealthFailure(ProviderProbeFailure):
    """Provider responded with an unhealthy service state."""

    kind = ProviderFailureKind.PROVIDER_HEALTH


class ProviderTransportFailure(ProviderProbeFailure):
    """Bounded connection transport failed before a provider response."""

    kind = ProviderFailureKind.TRANSPORT


class ProviderHealthStore:
    """Persist the latest bounded health snapshot per provider identity."""

    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "providers" / "health"

    def load(self, provider_id: str) -> ProviderHealthSnapshot | None:
        """Load the latest strict health snapshot for one provider."""
        _validate_identity(provider_id, field_name="provider id")
        path = self._path(provider_id)
        with exclusive_file_lock(self._lock_path(provider_id)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("provider health snapshot is unreadable") from exc
        snapshot = _health_from_dict(payload)
        if snapshot.provider.id != provider_id:
            raise ValueError("provider health identity mismatch")
        return snapshot

    def save(self, snapshot: ProviderHealthSnapshot) -> ProviderHealthSnapshot:
        """Atomically replace one provider's latest health evidence."""
        persisted = replace(snapshot, cached=False)
        with exclusive_file_lock(self._lock_path(snapshot.provider.id)):
            _atomic_private_json(
                self._path(snapshot.provider.id),
                _health_to_dict(persisted),
            )
        return persisted

    def _path(self, provider_id: str) -> Path:
        return self.root / f"{_hashed_key(provider_id)}.json"

    def _lock_path(self, provider_id: str) -> Path:
        return self.root / f".{_hashed_key(provider_id)}.lock"


class ProviderHealthService:
    """Apply network policy before bounded, provider-specific health I/O."""

    def __init__(
        self,
        backend: ProviderProbeBackend,
        store: ProviderHealthStore,
        *,
        network_policy: Callable[[ProviderProbeRequest], ProviderNetworkPolicyDecision]
        | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.backend = backend
        self.store = store
        self.network_policy = network_policy or _default_network_policy
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic

    def check(
        self,
        entry: ProviderRegistryEntry,
        *,
        discover_models: bool = True,
        timeout_seconds: float = 10.0,
        force: bool = False,
    ) -> ProviderHealthSnapshot:
        """Check one enabled provider or reuse its revision-bound TTL cache."""
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= MAX_PROVIDER_CHECK_TIMEOUT_SECONDS
        ):
            raise ValueError("provider check timeout is outside the bounded range")
        if not isinstance(discover_models, bool) or not isinstance(force, bool):
            raise ValueError("provider check flags must be booleans")
        profile = entry.profile
        request = ProviderProbeRequest(
            profile=profile,
            timeout_seconds=float(timeout_seconds),
            discover_models=discover_models,
            proxy_policy_ref=profile.proxy_policy_ref,
            tls_policy_ref=profile.tls_policy_ref,
            egress_policy_ref=profile.egress_policy_ref,
        )
        started = self._monotonic()
        if not entry.enabled:
            return self._save_failure(
                profile,
                started=started,
                kind=ProviderFailureKind.NETWORK_POLICY,
                reason_code="provider_disabled",
                discover_models=discover_models,
            )
        if profile.offline:
            return self._save_failure(
                profile,
                started=started,
                kind=ProviderFailureKind.NETWORK_POLICY,
                reason_code="offline_mode",
                discover_models=discover_models,
            )
        decision = self.network_policy(request)
        if not isinstance(decision, ProviderNetworkPolicyDecision):
            raise TypeError("network policy must return ProviderNetworkPolicyDecision")
        if not decision.allowed:
            return self._save_failure(
                profile,
                started=started,
                kind=ProviderFailureKind.NETWORK_POLICY,
                reason_code=decision.reason_code or "network_policy_denied",
                discover_models=discover_models,
            )
        cached = self.store.load(profile.id)
        if not force and self._cache_is_current(
            profile,
            cached,
            discover_models=discover_models,
        ):
            return replace(cached, cached=True)
        try:
            response = self.backend.check(request)
            if not isinstance(response, ProviderProbeResponse):
                raise TypeError("provider backend must return ProviderProbeResponse")
        except ProviderProbeFailure as exc:
            return self._save_failure(
                profile,
                started=started,
                kind=exc.kind,
                reason_code=exc.reason_code,
                discover_models=discover_models,
            )
        discovery_status = ProviderDiscoveryStatus.NOT_REQUESTED
        discovery_reason = None
        discovered: tuple[str, ...] = ()
        if discover_models:
            if response.discovery_succeeded:
                discovery_status = ProviderDiscoveryStatus.SUCCEEDED
                discovered = response.models
            else:
                discovery_status = ProviderDiscoveryStatus.FAILED
                discovery_reason = response.discovery_reason_code
        models = _merge_model_evidence(profile, discovered)
        snapshot = ProviderHealthSnapshot(
            provider=profile.ref,
            status=ProviderHealthStatus.READY,
            checked_at=_format_timestamp(self._now()),
            duration_ms=_duration_ms(started, self._monotonic()),
            discovery_status=discovery_status,
            discovery_reason_code=discovery_reason,
            models=models,
        )
        return self.store.save(snapshot)

    def _save_failure(
        self,
        profile: ProviderProfile,
        *,
        started: float,
        kind: ProviderFailureKind,
        reason_code: str,
        discover_models: bool,
    ) -> ProviderHealthSnapshot:
        discovery_status = (
            ProviderDiscoveryStatus.FAILED
            if discover_models
            else ProviderDiscoveryStatus.NOT_REQUESTED
        )
        snapshot = ProviderHealthSnapshot(
            provider=profile.ref,
            status=(
                ProviderHealthStatus.BLOCKED
                if kind is ProviderFailureKind.NETWORK_POLICY
                else ProviderHealthStatus.UNHEALTHY
            ),
            checked_at=_format_timestamp(self._now()),
            duration_ms=_duration_ms(started, self._monotonic()),
            discovery_status=discovery_status,
            models=_merge_model_evidence(profile, ()),
            failure_kind=kind,
            reason_code=reason_code,
            discovery_reason_code=(reason_code if discover_models else None),
        )
        return self.store.save(snapshot)

    def _cache_is_current(
        self,
        profile: ProviderProfile,
        snapshot: ProviderHealthSnapshot | None,
        *,
        discover_models: bool,
    ) -> bool:
        if snapshot is None or snapshot.provider != profile.ref:
            return False
        if (
            discover_models
            and snapshot.discovery_status is ProviderDiscoveryStatus.NOT_REQUESTED
        ):
            return False
        ttl = profile.discovery_cache_ttl_seconds
        if ttl <= 0:
            return False
        age = (self._now() - _parse_timestamp(snapshot.checked_at)).total_seconds()
        return 0 <= age <= ttl


def _default_network_policy(
    request: ProviderProbeRequest,
) -> ProviderNetworkPolicyDecision:
    if request.profile.offline:
        return ProviderNetworkPolicyDecision(False, "offline_mode")
    if request.egress_policy_ref is not None:
        return ProviderNetworkPolicyDecision(False, "egress_policy_unresolved")
    return ProviderNetworkPolicyDecision(True)


from .registry_codec import (  # noqa: E402
    _atomic_private_json,
    _duration_ms,
    _format_timestamp,
    _hashed_key,
    _health_from_dict,
    _health_to_dict,
    _merge_model_evidence,
    _normalize_discovered_model_names,
    _normalize_models,
    _parse_timestamp,
    _validate_identity,
    _validate_model,
    _validate_optional_reason,
    _validate_reason,
)
