"""Public gateway proxy result models."""

from __future__ import annotations

from dataclasses import dataclass, field

from gigaloom.types import GigaChatApiMode


class ProxyRequestError(RuntimeError):
    """Raised when the local proxy request fails."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ProxyHealth:
    """Proxy health check result."""

    ok: bool
    url: str
    path: str | None = None
    status_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ModelDiscovery:
    """Model discovery result for CLI/UI."""

    ok: bool
    models: tuple[str, ...]
    source: str
    error: str | None = None


@dataclass(frozen=True)
class RouteProbe:
    """Route-level diagnostic result for doctor output."""

    ok: bool
    path: str
    method: str
    status_code: int | None = None
    detail: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SidecarPreflight:
    """Explain whether the local proxy sidecar can be started."""

    ok: bool
    reason: str


@dataclass(frozen=True)
class ProxyStartup:
    """Result of preparing a local proxy for a harness request."""

    ok: bool
    proxy_url: str | None = None
    started: bool = False
    api_key: str | None = None
    harness_model_key: str | None = field(default=None, repr=False)
    pid: int | None = None
    ownership_id: str | None = None
    health_path: str | None = None
    health_status_code: int | None = None
    detail: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProxyRoutePreflight:
    """Route-aware proxy preparation result for one CLI execution."""

    ok: bool
    proxy_url: str
    api_mode: GigaChatApiMode
    route_path: str
    startup: ProxyStartup
    status_code: int | None = None
    detail: str | None = None
    error: str | None = None

    @property
    def api_key(self) -> str | None:
        """Return the transient proxy key without serializing it as evidence."""
        return self.startup.api_key

    @property
    def harness_model_key(self) -> str | None:
        """Return the transient model-signing key without serializing it."""
        return self.startup.harness_model_key
