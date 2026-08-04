"""Managed gpt2giga sidecars composed on the existing native process owner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import time
from typing import Protocol
from urllib.parse import urlsplit

from gigaloom.contracts.operational_validation import canonical_json_bytes
from gigaloom.native.base import NativeCommandPlan
from gigaloom.native.launch.gateway_contracts import (
    GatewayMode,
    GatewayProfileV1,
    gateway_version_admitted,
)
from gigaloom.native.launch.gateway_discovery import (
    GatewayMachineTransport,
    UrlLibGatewayMachineTransport,
)
from gigaloom.native.process import (
    NativeProcessRef,
    NativeProcessStartError,
    NativeProcessStatus,
)


DEFAULT_GATEWAY_STARTUP_TIMEOUT_SECONDS = 15.0
DEFAULT_GATEWAY_STARTUP_POLL_SECONDS = 0.05
_FORBIDDEN_NATIVE_HOME_NAMES = frozenset({".codex", ".claude", ".gemini"})


class GatewaySidecarReason(str, Enum):
    """Content-free managed sidecar refusal and loss reasons."""

    PROFILE_NOT_MANAGED = "profile_not_managed"
    ARTIFACT_UNVERIFIED = "artifact_unverified"
    ARTIFACT_IDENTITY_MISMATCH = "artifact_identity_mismatch"
    EXECUTABLE_UNAVAILABLE = "executable_unavailable"
    MANAGED_ROOT_INVALID = "managed_root_invalid"
    MANAGED_ENDPOINT_INVALID = "managed_endpoint_invalid"
    STARTUP_CONFIG_FAILED = "startup_config_failed"
    PROCESS_START_FAILED = "process_start_failed"
    STARTUP_READINESS_TIMEOUT = "startup_readiness_timeout"
    PROCESS_LOST = "process_lost"
    LEASE_NOT_FOUND = "lease_not_found"


class GatewaySidecarStatus(str, Enum):
    """Managed gateway lease result."""

    STARTED = "started"
    REUSED = "reused"
    STOPPED = "stopped"
    LOST = "lost"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GatewayArtifactEvidenceV1:
    """Verified install receipt supplied by the artifact installation boundary."""

    distribution: str
    version: str
    artifact_sha256: str
    executable_path: str
    source: str
    verified: bool
    reason_id: str | None = None


@dataclass(frozen=True)
class ManagedGatewayLeaseV1:
    """Content-free binding from a gateway profile to an existing process lease."""

    gateway_id: str
    profile_digest: str
    status: GatewaySidecarStatus
    process_lease_ref: str | None
    managed_root: str | None
    startup_config_ref: str | None
    readiness_confirmed: bool
    reason: GatewaySidecarReason | None = None
    observed_artifact_sha256: str | None = None


def gateway_artifact_admitted(
    profile: GatewayProfileV1,
    artifact: GatewayArtifactEvidenceV1 | None,
) -> bool:
    """Admit verified registry evidence by public version and identity contracts."""
    return bool(
        artifact is not None
        and artifact.verified
        and artifact.reason_id is None
        and artifact.distribution == profile.distribution
        and gateway_version_admitted(artifact.version, profile.version_window)
        and len(artifact.artifact_sha256) == 64
        and all(
            character in "0123456789abcdef" for character in artifact.artifact_sha256
        )
        and artifact.executable_path
    )


class GatewayProcessLeaseOwner(Protocol):
    """Subset of the existing native process owner used by sidecars."""

    def start(
        self,
        plan: NativeCommandPlan,
        *,
        session_id: str,
        workspace: str | None = None,
        run_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> NativeProcessRef: ...

    def status(self, process_id: str) -> NativeProcessRef: ...

    def stop(self, process_id: str) -> NativeProcessRef: ...


class GatewayStartupReadinessProbe(Protocol):
    """Process-readiness check that must not invoke provider inference routes."""

    def startup_ready(self, base_url: str) -> bool: ...


class UrlLibGatewayStartupReadinessProbe:
    """Health-only startup probe; never calls models or inference routes."""

    def __init__(
        self,
        transport: GatewayMachineTransport | None = None,
        *,
        timeout_seconds: float = 1.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("gateway readiness timeout must be positive")
        self._transport = transport or UrlLibGatewayMachineTransport()
        self._timeout_seconds = timeout_seconds

    def startup_ready(self, base_url: str) -> bool:
        """Return health state without model discovery or provider traffic."""
        try:
            status, _ = self._transport.get_json(
                base_url,
                "/health",
                timeout_seconds=self._timeout_seconds,
            )
        except Exception:
            return False
        return status == 200


class ManagedGatewaySidecarService:
    """Create, warm-reuse, and stop sidecars through one injected lease owner."""

    def __init__(
        self,
        process_owner: GatewayProcessLeaseOwner,
        readiness_probe: GatewayStartupReadinessProbe | None = None,
        *,
        managed_data_root: str | os.PathLike[str],
        startup_timeout_seconds: float = DEFAULT_GATEWAY_STARTUP_TIMEOUT_SECONDS,
        poll_seconds: float = DEFAULT_GATEWAY_STARTUP_POLL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if startup_timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("gateway startup timing bounds must be positive")
        self._process_owner = process_owner
        self._readiness_probe = readiness_probe or UrlLibGatewayStartupReadinessProbe()
        self._managed_data_root = _validated_managed_root(managed_data_root)
        self._startup_timeout_seconds = startup_timeout_seconds
        self._poll_seconds = min(poll_seconds, startup_timeout_seconds)
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._leases: dict[tuple[str, str], tuple[str, str]] = {}

    def ensure_started(
        self,
        profile: GatewayProfileV1,
        artifact: GatewayArtifactEvidenceV1,
        *,
        environment: Mapping[str, str],
        session_id: str,
        run_id: str,
    ) -> ManagedGatewayLeaseV1:
        """Return a healthy warm lease or start the exact verified artifact."""
        refusal = _validate_start_request(profile, artifact)
        if refusal is not None:
            return _blocked(profile, refusal)
        key = (profile.gateway_id, profile.profile_digest)
        existing_lease = self._leases.get(key)
        if existing_lease is not None:
            existing_id, observed_digest = existing_lease
            try:
                existing = self._process_owner.status(existing_id)
            except (KeyError, RuntimeError):
                existing = None
            if (
                existing is not None
                and existing.status is NativeProcessStatus.RUNNING
                and observed_digest == artifact.artifact_sha256
                and self._readiness_probe.startup_ready(profile.base_url)
            ):
                return _lease(
                    profile,
                    GatewaySidecarStatus.REUSED,
                    existing.id,
                    self._profile_root(profile),
                    readiness_confirmed=True,
                    observed_artifact_sha256=observed_digest,
                )
            self._leases.pop(key, None)
            if existing is not None and existing.status is NativeProcessStatus.RUNNING:
                try:
                    self._process_owner.stop(existing.id)
                except (KeyError, RuntimeError):
                    return _blocked(profile, GatewaySidecarReason.PROCESS_LOST)
        profile_root = self._profile_root(profile)
        try:
            profile_root.mkdir(parents=True, exist_ok=True)
            profile_root.chmod(0o700)
            config_path = self._write_startup_config(
                profile,
                artifact,
                profile_root,
                environment,
            )
        except OSError:
            return _blocked(profile, GatewaySidecarReason.STARTUP_CONFIG_FAILED)
        try:
            executable = Path(artifact.executable_path).resolve(strict=True)
        except OSError:
            return _blocked(profile, GatewaySidecarReason.EXECUTABLE_UNAVAILABLE)
        plan = NativeCommandPlan(
            command=_gateway_command(profile, executable),
            display_command=_gateway_command(profile, executable),
            env=dict(environment),
            cwd=os.fspath(profile_root),
            native_home=os.fspath(profile_root),
            metadata={
                "harness_id": "gpt2giga-gateway",
                "gateway_id": profile.gateway_id,
                "profile_digest": profile.profile_digest,
                "artifact_sha256": artifact.artifact_sha256,
                "startup_config_ref": f"managed-config:{config_path.name}",
                "managed_sidecar": True,
            },
        )
        try:
            process = self._process_owner.start(
                plan,
                session_id=session_id,
                run_id=run_id,
            )
        except (KeyError, NativeProcessStartError, OSError, RuntimeError):
            return _blocked(profile, GatewaySidecarReason.PROCESS_START_FAILED)
        self._leases[key] = (process.id, artifact.artifact_sha256)
        if not self._wait_until_ready(profile.base_url, process.id):
            try:
                self._process_owner.stop(process.id)
            except (KeyError, RuntimeError):
                pass
            self._leases.pop(key, None)
            return _blocked(profile, GatewaySidecarReason.STARTUP_READINESS_TIMEOUT)
        return _lease(
            profile,
            GatewaySidecarStatus.STARTED,
            process.id,
            profile_root,
            readiness_confirmed=True,
            observed_artifact_sha256=artifact.artifact_sha256,
        )

    def status(self, profile: GatewayProfileV1) -> ManagedGatewayLeaseV1:
        """Project the current owned lease without starting a process."""
        key = (profile.gateway_id, profile.profile_digest)
        lease_binding = self._leases.get(key)
        if lease_binding is None:
            return _blocked(profile, GatewaySidecarReason.LEASE_NOT_FOUND)
        process_id, observed_digest = lease_binding
        try:
            process = self._process_owner.status(process_id)
        except (KeyError, RuntimeError):
            process = None
        if process is None or process.status is not NativeProcessStatus.RUNNING:
            self._leases.pop(key, None)
            return ManagedGatewayLeaseV1(
                gateway_id=profile.gateway_id,
                profile_digest=profile.profile_digest,
                status=GatewaySidecarStatus.LOST,
                process_lease_ref=f"native-process:{process_id}",
                managed_root=os.fspath(self._profile_root(profile)),
                startup_config_ref="managed-config:startup.json",
                readiness_confirmed=False,
                reason=GatewaySidecarReason.PROCESS_LOST,
                observed_artifact_sha256=observed_digest,
            )
        ready = self._readiness_probe.startup_ready(profile.base_url)
        return _lease(
            profile,
            GatewaySidecarStatus.REUSED,
            process_id,
            self._profile_root(profile),
            readiness_confirmed=ready,
            observed_artifact_sha256=observed_digest,
        )

    def stop(self, profile: GatewayProfileV1) -> ManagedGatewayLeaseV1:
        """Stop only the process lease owned for this exact profile digest."""
        key = (profile.gateway_id, profile.profile_digest)
        lease_binding = self._leases.pop(key, None)
        if lease_binding is None:
            return _blocked(profile, GatewaySidecarReason.LEASE_NOT_FOUND)
        process_id, observed_digest = lease_binding
        try:
            self._process_owner.stop(process_id)
        except (KeyError, RuntimeError):
            return ManagedGatewayLeaseV1(
                gateway_id=profile.gateway_id,
                profile_digest=profile.profile_digest,
                status=GatewaySidecarStatus.LOST,
                process_lease_ref=f"native-process:{process_id}",
                managed_root=os.fspath(self._profile_root(profile)),
                startup_config_ref="managed-config:startup.json",
                readiness_confirmed=False,
                reason=GatewaySidecarReason.PROCESS_LOST,
                observed_artifact_sha256=observed_digest,
            )
        return _lease(
            profile,
            GatewaySidecarStatus.STOPPED,
            process_id,
            self._profile_root(profile),
            readiness_confirmed=False,
            observed_artifact_sha256=observed_digest,
        )

    def _profile_root(self, profile: GatewayProfileV1) -> Path:
        return (
            self._managed_data_root
            / "gateways"
            / profile.gateway_id
            / profile.profile_digest[:16]
        )

    def _write_startup_config(
        self,
        profile: GatewayProfileV1,
        artifact: GatewayArtifactEvidenceV1,
        profile_root: Path,
        environment: Mapping[str, str],
    ) -> Path:
        target = profile_root / "startup.json"
        temporary = profile_root / ".startup.json.tmp"
        payload = {
            "schema_version": "gigaloom.gateway-startup.v1",
            "gateway_id": profile.gateway_id,
            "profile_digest": profile.profile_digest,
            "distribution": artifact.distribution,
            "version": artifact.version,
            "artifact_sha256": artifact.artifact_sha256,
            "source": artifact.source,
            "base_url": profile.base_url,
            "startup_config_revision": profile.startup_config_revision,
            "environment": {
                name: "<redacted>" if _secret_like(name) else "<provided>"
                for name in sorted(environment)
            },
        }
        temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
        temporary.chmod(0o600)
        temporary.replace(target)
        return target

    def _wait_until_ready(self, base_url: str, process_id: str) -> bool:
        deadline = self._monotonic() + self._startup_timeout_seconds
        while self._monotonic() <= deadline:
            try:
                status = self._process_owner.status(process_id)
            except (KeyError, RuntimeError):
                return False
            if status.status is not NativeProcessStatus.RUNNING:
                return False
            if self._readiness_probe.startup_ready(base_url):
                return True
            self._sleeper(self._poll_seconds)
        return False


def _validate_start_request(
    profile: GatewayProfileV1,
    artifact: GatewayArtifactEvidenceV1,
) -> GatewaySidecarReason | None:
    if profile.mode is not GatewayMode.MANAGED:
        return GatewaySidecarReason.PROFILE_NOT_MANAGED
    try:
        _gateway_endpoint(profile)
    except ValueError:
        return GatewaySidecarReason.MANAGED_ENDPOINT_INVALID
    if not artifact.verified:
        return GatewaySidecarReason.ARTIFACT_UNVERIFIED
    if not gateway_artifact_admitted(profile, artifact):
        return GatewaySidecarReason.ARTIFACT_IDENTITY_MISMATCH
    executable = Path(artifact.executable_path)
    try:
        resolved = executable.resolve(strict=True)
    except OSError:
        return GatewaySidecarReason.EXECUTABLE_UNAVAILABLE
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return GatewaySidecarReason.EXECUTABLE_UNAVAILABLE
    executable_name = resolved.name.removesuffix(".exe")
    if executable_name != profile.executable:
        return GatewaySidecarReason.ARTIFACT_IDENTITY_MISMATCH
    return None


def _validated_managed_root(value: str | os.PathLike[str]) -> Path:
    root = Path(value)
    if not root.is_absolute() or _FORBIDDEN_NATIVE_HOME_NAMES.intersection(root.parts):
        raise ValueError(GatewaySidecarReason.MANAGED_ROOT_INVALID.value)
    return root


def _gateway_command(profile: GatewayProfileV1, executable: Path) -> tuple[str, ...]:
    host, port = _gateway_endpoint(profile)
    return (
        os.fspath(executable),
        "--proxy.host",
        host,
        "--proxy.port",
        str(port),
    )


def _gateway_endpoint(profile: GatewayProfileV1) -> tuple[str, int]:
    parsed = urlsplit(profile.base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("managed gateway base URL must be loopback")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ValueError("managed gateway port is invalid") from error
    return parsed.hostname, port


def _secret_like(name: str) -> bool:
    upper = name.upper()
    return any(
        part in upper for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    )


def _blocked(
    profile: GatewayProfileV1,
    reason: GatewaySidecarReason,
) -> ManagedGatewayLeaseV1:
    return ManagedGatewayLeaseV1(
        gateway_id=profile.gateway_id,
        profile_digest=profile.profile_digest,
        status=GatewaySidecarStatus.BLOCKED,
        process_lease_ref=None,
        managed_root=None,
        startup_config_ref=None,
        readiness_confirmed=False,
        reason=reason,
    )


def _lease(
    profile: GatewayProfileV1,
    status: GatewaySidecarStatus,
    process_id: str,
    profile_root: Path,
    *,
    readiness_confirmed: bool,
    observed_artifact_sha256: str,
) -> ManagedGatewayLeaseV1:
    return ManagedGatewayLeaseV1(
        gateway_id=profile.gateway_id,
        profile_digest=profile.profile_digest,
        status=status,
        process_lease_ref=f"native-process:{process_id}",
        managed_root=os.fspath(profile_root),
        startup_config_ref="managed-config:startup.json",
        readiness_confirmed=readiness_confirmed,
        observed_artifact_sha256=observed_artifact_sha256,
    )
