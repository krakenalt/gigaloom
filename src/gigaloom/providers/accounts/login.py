"""Provider-native account discovery and login orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
import os
from pathlib import Path
import secrets
import stat
import threading
from typing import Any
from uuid import uuid4

from gigaloom.cli_capabilities import CliCapabilitySnapshot
from gigaloom.executables import ExecutableResolution
from gigaloom.providers.authentication.capabilities import (
    ProviderAuthenticationEvidence,
    load_provider_authentication_evidence,
    provider_authentication_surface_proven,
)
from gigaloom.sessions.contracts import exclusive_file_lock

from .broker import (
    AUTH_COMMAND_TIMEOUT_SECONDS,
    AUTH_STATUS_TIMEOUT_SECONDS,
    _INHERITED_ENVIRONMENT,
    BoundedAuthenticationCommandRunner,
    AuthenticationCommandRunner,
    ProviderAccountSnapshot,
    ProviderAccountStatus,
    ProviderAuthenticationConflictError,
    ProviderAuthenticationOperationError,
    ProviderSessionBinding,
    _LoginAttempt,
    _bounded_text,
    _classify_status,
    _expiry,
    _finished_attempt,
    _opaque_identity,
    _operation_command,
    _source,
    _utc_now,
    provider_account_snapshot_to_dict,
)
from .gemini_acp import GeminiAcpAuthenticationRunner


class NativeLoginBroker:
    """Guide and observe native login without reading provider credential stores."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        resolution_provider: Callable[[str], ExecutableResolution],
        capability_provider: Callable[[str], CliCapabilitySnapshot],
        evidence: ProviderAuthenticationEvidence | None = None,
        runner: AuthenticationCommandRunner | None = None,
        gemini_acp_runner: GeminiAcpAuthenticationRunner | None = None,
        login_timeout_seconds: float = AUTH_COMMAND_TIMEOUT_SECONDS,
        status_timeout_seconds: float = AUTH_STATUS_TIMEOUT_SECONDS,
    ) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "provider_authentication"
        self.resolution_provider = resolution_provider
        self.capability_provider = capability_provider
        self.evidence = evidence or load_provider_authentication_evidence()
        self.runner = runner or BoundedAuthenticationCommandRunner()
        self.gemini_acp_runner = gemini_acp_runner or GeminiAcpAuthenticationRunner()
        self.login_timeout_seconds = max(float(login_timeout_seconds), 0.05)
        self.status_timeout_seconds = max(float(status_timeout_seconds), 0.05)
        self._contracts = {
            str(item["harness_id"]): item for item in self.evidence.providers
        }
        self._attempts: dict[str, _LoginAttempt] = {}
        self._latest: dict[str, ProviderAccountSnapshot] = {}
        self._lock = threading.RLock()

    def list_accounts(self) -> dict[str, Any]:
        """Return all reviewed account cards after bounded status observation."""
        return {
            "schema_version": 1,
            "credential_values_readable": False,
            "real_native_homes_accessed": False,
            "accounts": [
                provider_account_snapshot_to_dict(self.status(provider_id))
                for provider_id in self._contracts
            ],
        }

    def status(self, provider_id: str) -> ProviderAccountSnapshot:
        """Return the current attempt or observe status when no attempt exists."""
        contract = self._contract(provider_id)
        with self._lock:
            attempt = self._attempts.get(provider_id)
            if attempt is not None:
                return attempt.snapshot
        snapshot = self._observe_status(contract)
        with self._lock:
            self._latest[provider_id] = snapshot
        return snapshot

    def refresh(self, provider_id: str) -> ProviderAccountSnapshot:
        """Explicitly replace the latest attempt outcome with provider status."""
        contract = self._contract(provider_id)
        with self._lock:
            attempt = self._attempts.get(provider_id)
            if (
                attempt is not None
                and attempt.snapshot.status is ProviderAccountStatus.PENDING
            ):
                raise ProviderAuthenticationConflictError(
                    "provider login is still pending"
                )
        snapshot = self._observe_status(contract, ignore_pending=True)
        with self._lock:
            self._attempts.pop(provider_id, None)
            self._latest[provider_id] = snapshot
        return snapshot

    def session_binding(self, provider_id: str) -> ProviderSessionBinding | None:
        """Return an opaque binding only when provider status is currently ready."""
        snapshot = self.status(provider_id)
        if snapshot.status is not ProviderAccountStatus.READY:
            return None
        key = self._binding_identity_key()
        home_identity = _opaque_identity(
            "home",
            key,
            provider_id,
            os.fspath(self._home(provider_id)),
        )
        identity_label = snapshot.identity_label or "identity-undisclosed"
        identity_evidence = (
            "provider_reported"
            if snapshot.identity_label is not None
            else "isolated_home_scoped"
        )
        account_identity = _opaque_identity(
            "account",
            key,
            provider_id,
            home_identity,
            self._binding_generation(provider_id).hex(),
            identity_label,
            snapshot.authentication_method or "method-undisclosed",
        )
        source_identity = _opaque_identity(
            "source",
            key,
            provider_id,
            snapshot.source,
            snapshot.pinned_cli_version,
            snapshot.detected_cli_version or "version-undetected",
            snapshot.version_status,
        )
        return ProviderSessionBinding(
            provider_id=provider_id,
            account_identity=account_identity,
            home_identity=home_identity,
            source_identity=source_identity,
            identity_evidence=identity_evidence,
            authentication_method=snapshot.authentication_method,
            observed_at=snapshot.checked_at,
        )

    def start(self, provider_id: str) -> ProviderAccountSnapshot:
        """Start one bounded provider-owned login attempt in the background."""
        contract = self._contract(provider_id)
        resolution, capability, unavailable = self._admission(contract)
        if unavailable is not None:
            raise ProviderAuthenticationOperationError(unavailable.reason_code)
        command = _operation_command(provider_id, "start")
        if command is None:
            raise ProviderAuthenticationOperationError("login_start_unavailable")
        assert resolution is not None
        assert capability is not None
        with self._lock:
            current = self._attempts.get(provider_id)
            if (
                current is not None
                and current.snapshot.status is ProviderAccountStatus.PENDING
            ):
                raise ProviderAuthenticationConflictError(
                    "provider login is already pending"
                )
            attempt_id = f"login_{uuid4().hex}"
            pending = self._snapshot(
                contract,
                capability=capability,
                status=ProviderAccountStatus.PENDING,
                source=_source(provider_id, "start"),
                reason_code="provider_login_pending",
                attempt_id=attempt_id,
            )
            attempt = _LoginAttempt(
                id=attempt_id,
                provider_id=provider_id,
                cancel_event=threading.Event(),
                snapshot=pending,
            )
            self._attempts[provider_id] = attempt
            self._latest[provider_id] = pending
            thread = threading.Thread(
                target=self._run_login,
                args=(attempt, resolution, command),
                daemon=True,
                name=f"gigaloom-login-{provider_id}",
            )
            attempt.thread = thread
            thread.start()
            return pending

    def cancel(self, provider_id: str) -> ProviderAccountSnapshot:
        """Cancel the exact pending broker attempt, if any."""
        contract = self._contract(provider_id)
        with self._lock:
            attempt = self._attempts.get(provider_id)
            if (
                attempt is None
                or attempt.snapshot.status is not ProviderAccountStatus.PENDING
            ):
                raise ProviderAuthenticationOperationError("provider_login_not_pending")
            attempt.cancel_event.set()
            cancelled = _finished_attempt(
                attempt.snapshot,
                status=ProviderAccountStatus.LOGGED_OUT,
                reason_code="provider_login_cancelled",
                recovery=tuple(contract["recovery"]),
            )
            attempt.snapshot = cancelled
            self._latest[provider_id] = cancelled
            return cancelled

    def logout(self, provider_id: str) -> ProviderAccountSnapshot:
        """Run the reviewed provider-owned logout command in the isolated home."""
        contract = self._contract(provider_id)
        with self._lock:
            attempt = self._attempts.get(provider_id)
            if (
                attempt is not None
                and attempt.snapshot.status is ProviderAccountStatus.PENDING
            ):
                raise ProviderAuthenticationConflictError(
                    "provider login is still pending"
                )
        resolution, capability, unavailable = self._admission(contract)
        if unavailable is not None:
            raise ProviderAuthenticationOperationError(unavailable.reason_code)
        command = _operation_command(provider_id, "logout")
        if command is None:
            raise ProviderAuthenticationOperationError("logout_unavailable")
        assert resolution is not None
        assert capability is not None
        result = self.runner.run(
            (*resolution.command, *command),
            environment=self._isolated_environment(provider_id),
            cwd=self._home(provider_id),
            timeout_seconds=self.status_timeout_seconds,
            cancel_event=threading.Event(),
            capture_output=False,
        )
        self._binding_generation(provider_id, rotate=True)
        if result.timed_out:
            status = ProviderAccountStatus.UNKNOWN
            reason_code = "logout_timed_out"
        elif result.returncode == 0:
            status = ProviderAccountStatus.LOGGED_OUT
            reason_code = "provider_logout_complete"
        else:
            status = ProviderAccountStatus.UNKNOWN
            reason_code = "provider_logout_failed"
        snapshot = self._snapshot(
            contract,
            capability=capability,
            status=status,
            source=_source(provider_id, "logout"),
            reason_code=reason_code,
        )
        with self._lock:
            self._attempts.pop(provider_id, None)
            self._latest[provider_id] = snapshot
        return snapshot

    def _run_login(
        self,
        attempt: _LoginAttempt,
        resolution: ExecutableResolution,
        command: tuple[str, ...],
    ) -> None:
        if attempt.provider_id == "gemini-cli":
            result = self.gemini_acp_runner.run(
                (*resolution.command, *command),
                environment=self._isolated_environment(attempt.provider_id),
                cwd=self._home(attempt.provider_id),
                timeout_seconds=self.login_timeout_seconds,
                cancel_event=attempt.cancel_event,
            )
        else:
            result = self.runner.run(
                (*resolution.command, *command),
                environment=self._isolated_environment(attempt.provider_id),
                cwd=self._home(attempt.provider_id),
                timeout_seconds=self.login_timeout_seconds,
                cancel_event=attempt.cancel_event,
                capture_output=False,
            )
        contract = self._contract(attempt.provider_id)
        if result.cancelled or attempt.cancel_event.is_set():
            final = _finished_attempt(
                attempt.snapshot,
                status=ProviderAccountStatus.LOGGED_OUT,
                reason_code="provider_login_cancelled",
                recovery=tuple(contract["recovery"]),
            )
        elif result.timed_out:
            final = _finished_attempt(
                attempt.snapshot,
                status=ProviderAccountStatus.UNKNOWN,
                reason_code="provider_login_timed_out",
                recovery=tuple(contract["recovery"]),
            )
        elif result.returncode != 0:
            final = _finished_attempt(
                attempt.snapshot,
                status=ProviderAccountStatus.LOGGED_OUT,
                reason_code="provider_login_failed",
                recovery=tuple(contract["recovery"]),
            )
        elif attempt.provider_id == "gemini-cli":
            final = _finished_attempt(
                attempt.snapshot,
                status=ProviderAccountStatus.READY,
                reason_code="provider_ready",
                recovery=tuple(contract["recovery"]),
            )
            final = replace(final, authentication_method="google_account")
            self._binding_generation(attempt.provider_id, rotate=True)
        else:
            final = self._observe_status(contract, ignore_pending=True)
            if final.status is ProviderAccountStatus.UNKNOWN:
                final = replace(final, reason_code="provider_login_status_unknown")
            self._binding_generation(attempt.provider_id, rotate=True)
        final = replace(final, attempt_id=attempt.id)
        with self._lock:
            current = self._attempts.get(attempt.provider_id)
            if current is attempt:
                attempt.snapshot = final
                self._latest[attempt.provider_id] = final

    def _observe_status(
        self,
        contract: Mapping[str, Any],
        *,
        ignore_pending: bool = False,
    ) -> ProviderAccountSnapshot:
        provider_id = str(contract["harness_id"])
        if not ignore_pending:
            with self._lock:
                attempt = self._attempts.get(provider_id)
                if (
                    attempt is not None
                    and attempt.snapshot.status is ProviderAccountStatus.PENDING
                ):
                    return attempt.snapshot
        resolution, capability, unavailable = self._admission(contract)
        if unavailable is not None:
            return unavailable
        command = _operation_command(provider_id, "status")
        assert capability is not None
        if command is None:
            return self._snapshot(
                contract,
                capability=capability,
                status=ProviderAccountStatus.UNKNOWN,
                source=_source(provider_id, "status"),
                reason_code="machine_status_unavailable",
            )
        assert resolution is not None
        result = self.runner.run(
            (*resolution.command, *command),
            environment=self._isolated_environment(provider_id),
            cwd=self._home(provider_id),
            timeout_seconds=self.status_timeout_seconds,
            cancel_event=threading.Event(),
            capture_output=True,
        )
        status, identity, method, expiry, reason_code = _classify_status(
            provider_id,
            result,
        )
        return self._snapshot(
            contract,
            capability=capability,
            status=status,
            source=_source(provider_id, "status"),
            reason_code=reason_code,
            identity_label=identity,
            authentication_method=method,
            expires_at=expiry,
        )

    def _admission(
        self,
        contract: Mapping[str, Any],
    ) -> tuple[
        ExecutableResolution | None,
        CliCapabilitySnapshot | None,
        ProviderAccountSnapshot | None,
    ]:
        provider_id = str(contract["harness_id"])
        resolution = self.resolution_provider(provider_id)
        capability = self.capability_provider(provider_id)
        exact_pin = resolution.available and provider_authentication_surface_proven(
            contract, capability
        )
        if exact_pin:
            return resolution, capability, None
        if not resolution.available or capability.status == "missing":
            reason_code = "provider_cli_missing"
        elif capability.parsed_version != contract["pinned_cli_version"]:
            reason_code = "provider_cli_version_drift"
        else:
            reason_code = "provider_cli_capability_unproven"
        return (
            None,
            capability,
            self._snapshot(
                contract,
                capability=capability,
                status=ProviderAccountStatus.UNAVAILABLE,
                source="reviewed_provider_authentication_evidence_v1",
                reason_code=reason_code,
            ),
        )

    def _snapshot(
        self,
        contract: Mapping[str, Any],
        *,
        capability: CliCapabilitySnapshot,
        status: ProviderAccountStatus,
        source: str,
        reason_code: str,
        identity_label: str | None = None,
        authentication_method: str | None = None,
        expires_at: str | None = None,
        attempt_id: str | None = None,
    ) -> ProviderAccountSnapshot:
        provider_id = str(contract["harness_id"])
        exact_pin = provider_authentication_surface_proven(contract, capability)
        start_supported = (
            exact_pin and _operation_command(provider_id, "start") is not None
        )
        status_supported = (
            exact_pin and _operation_command(provider_id, "status") is not None
        )
        logout_supported = (
            exact_pin and _operation_command(provider_id, "logout") is not None
        )
        return ProviderAccountSnapshot(
            provider_id=provider_id,
            display_name=str(contract["display_name"]),
            status=status,
            source=source,
            checked_at=_utc_now(),
            pinned_cli_version=str(contract["pinned_cli_version"]),
            detected_cli_version=capability.parsed_version,
            version_status=(
                "reviewed_pin"
                if exact_pin and capability.compatible
                else capability.version_window_status
            ),
            identity_label=_bounded_text(identity_label),
            authentication_method=_bounded_text(authentication_method),
            expires_at=_expiry(expiry=expires_at),
            reason_code=reason_code,
            recovery=tuple(str(item) for item in contract["recovery"]),
            actions={
                "start": start_supported,
                "status": status_supported,
                "logout": logout_supported,
                "cancel": status is ProviderAccountStatus.PENDING,
            },
            attempt_id=attempt_id,
        )

    def _contract(self, provider_id: str) -> Mapping[str, Any]:
        try:
            return self._contracts[provider_id]
        except KeyError as exc:
            raise ProviderAuthenticationOperationError(
                "provider_authentication_unknown"
            ) from exc

    def _home(self, provider_id: str) -> Path:
        home = self.root / "homes" / provider_id
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with suppress(OSError):
            home.chmod(0o700)
        return home

    def _isolated_environment(self, provider_id: str) -> dict[str, str]:
        source = os.environ
        environment = {
            name: value
            for name in _INHERITED_ENVIRONMENT
            if (value := source.get(name)) is not None
        }
        home = self._home(provider_id)
        environment.update(
            {
                "HOME": os.fspath(home),
                "NO_COLOR": "1",
                "DO_NOT_TRACK": "1",
            }
        )
        if provider_id == "codex-cli":
            environment["CODEX_HOME"] = os.fspath(home / ".codex")
        elif provider_id == "claude-code":
            environment["CLAUDE_CONFIG_DIR"] = os.fspath(home / ".claude")
        elif provider_id == "gemini-cli":
            environment["GEMINI_CLI_HOME"] = os.fspath(home / ".gemini")
            environment["GEMINI_TELEMETRY_ENABLED"] = "false"
        return environment

    def _binding_identity_key(self) -> bytes:
        path = self.root / "binding_identity.key"
        return self._private_identity_bytes(path)

    def _binding_generation(self, provider_id: str, *, rotate: bool = False) -> bytes:
        path = self.root / "bindings" / f"{provider_id}.generation"
        return self._private_identity_bytes(path, rotate=rotate)

    def _private_identity_bytes(self, path: Path, *, rotate: bool = False) -> bytes:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with exclusive_file_lock(path):
                file_status = None
                if not rotate:
                    with suppress(FileNotFoundError):
                        file_status = path.lstat()
                if file_status is None:
                    key = secrets.token_bytes(32)
                    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
                    descriptor = os.open(
                        temporary,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                    try:
                        os.write(descriptor, key)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    os.replace(temporary, path)
                else:
                    if not stat.S_ISREG(file_status.st_mode):
                        raise ProviderAuthenticationOperationError(
                            "provider_binding_identity_key_invalid"
                        )
                    descriptor = os.open(
                        path,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                            raise ProviderAuthenticationOperationError(
                                "provider_binding_identity_key_invalid"
                            )
                        if hasattr(os, "fchmod"):
                            os.fchmod(descriptor, 0o600)
                        key = os.read(descriptor, 33)
                    finally:
                        os.close(descriptor)
            if len(key) != 32:
                raise ProviderAuthenticationOperationError(
                    "provider_binding_identity_key_invalid"
                )
            return key
