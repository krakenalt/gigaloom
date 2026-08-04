"""Bounded provider-owned authentication broker with isolated native homes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Any, Protocol


AUTH_COMMAND_TIMEOUT_SECONDS = 180.0
AUTH_STATUS_TIMEOUT_SECONDS = 5.0
AUTH_OUTPUT_BYTES = 64 * 1024
_INHERITED_ENVIRONMENT = (
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TERM",
    "CODEX_CA_CERTIFICATE",
    "SSL_CERT_FILE",
)
_EXPIRY_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class ProviderAccountStatus(str, Enum):
    """Public provider-account states admitted by the login broker."""

    LOGGED_OUT = "logged_out"
    PENDING = "pending"
    READY = "ready"
    EXPIRED = "expired"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderAuthenticationBrokerError(RuntimeError):
    """Base error for bounded provider-owned authentication operations."""


class ProviderAuthenticationConflictError(ProviderAuthenticationBrokerError):
    """Raised when a second login attempts to replace an active one."""


class ProviderAuthenticationOperationError(ProviderAuthenticationBrokerError):
    """Raised when the requested provider operation is not admitted."""


@dataclass(frozen=True)
class AuthenticationCommandResult:
    """Bounded result retained only for immediate status classification."""

    returncode: int
    stdout: bytes = b""
    timed_out: bool = False
    cancelled: bool = False


class AuthenticationCommandRunner(Protocol):
    """Execute one provider-owned command without shell interpolation."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
        capture_output: bool,
    ) -> AuthenticationCommandResult:
        """Run one explicit argv in an isolated provider-owned home."""


@dataclass(frozen=True)
class ProviderAccountSnapshot:
    """Content-free account card projection."""

    provider_id: str
    display_name: str
    status: ProviderAccountStatus
    source: str
    checked_at: str
    pinned_cli_version: str
    detected_cli_version: str | None
    version_status: str
    identity_label: str | None
    authentication_method: str | None
    expires_at: str | None
    reason_code: str
    recovery: tuple[str, ...]
    actions: Mapping[str, bool]
    attempt_id: str | None = None
    home_scope: str = "isolated_provider_owned"


@dataclass(frozen=True)
class ProviderSessionBinding:
    """Opaque account and home identity admitted for session continuity."""

    provider_id: str
    account_identity: str
    home_identity: str
    source_identity: str
    identity_evidence: str
    authentication_method: str | None
    observed_at: str
    quota_status: str = "provider_owned_unobserved"
    monetary_cost_status: str = "api_route_separate"
    schema_version: int = 1


@dataclass
class _LoginAttempt:
    id: str
    provider_id: str
    cancel_event: threading.Event
    snapshot: ProviderAccountSnapshot
    thread: threading.Thread | None = None


class BoundedAuthenticationCommandRunner:
    """Subprocess runner that drains but never retains unbounded output."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
        capture_output: bool,
    ) -> AuthenticationCommandResult:
        command = tuple(_validated_argument(value) for value in argv)
        stdout_target: Any = subprocess.PIPE if capture_output else subprocess.DEVNULL
        try:
            process = subprocess.Popen(
                command,
                cwd=os.fspath(cwd),
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=stdout_target,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=os.name != "nt",
            )
        except OSError:
            return AuthenticationCommandResult(returncode=126)

        output = bytearray()
        reader = None
        if process.stdout is not None:
            reader = threading.Thread(
                target=_drain_output,
                args=(process.stdout, output),
                daemon=True,
                name="gigaloom-provider-auth-output",
            )
            reader.start()

        deadline = time.monotonic() + max(float(timeout_seconds), 0.05)
        timed_out = False
        cancelled = False
        while process.poll() is None:
            if cancel_event.wait(0.05):
                cancelled = True
                _stop_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _stop_process(process)
                break
        try:
            returncode = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            returncode = process.wait(timeout=1.0)
        if reader is not None:
            reader.join(timeout=1.0)
        return AuthenticationCommandResult(
            returncode=returncode,
            stdout=bytes(output),
            timed_out=timed_out,
            cancelled=cancelled,
        )


def provider_account_snapshot_to_dict(
    snapshot: ProviderAccountSnapshot,
) -> dict[str, Any]:
    """Serialize one account card without commands, paths, or provider output."""
    return {
        "provider_id": snapshot.provider_id,
        "display_name": snapshot.display_name,
        "status": snapshot.status.value,
        "source": snapshot.source,
        "checked_at": snapshot.checked_at,
        "pinned_cli_version": snapshot.pinned_cli_version,
        "detected_cli_version": snapshot.detected_cli_version,
        "version_status": snapshot.version_status,
        "identity_label": snapshot.identity_label,
        "authentication_method": snapshot.authentication_method,
        "expires_at": snapshot.expires_at,
        "reason_code": snapshot.reason_code,
        "recovery": list(snapshot.recovery),
        "actions": dict(snapshot.actions),
        "attempt_id": snapshot.attempt_id,
        "home_scope": snapshot.home_scope,
        "credential_values_readable": False,
    }


def provider_session_binding_to_dict(
    binding: ProviderSessionBinding,
) -> dict[str, Any]:
    """Serialize a path-free binding with separate quota and cost ownership."""
    return {
        "schema_version": binding.schema_version,
        "provider_id": binding.provider_id,
        "account_identity": binding.account_identity,
        "home_identity": binding.home_identity,
        "source_identity": binding.source_identity,
        "identity_evidence": binding.identity_evidence,
        "authentication_method": binding.authentication_method,
        "observed_at": binding.observed_at,
        "quota": {
            "ownership": "provider",
            "status": binding.quota_status,
        },
        "monetary_cost": {
            "ownership": "api_route",
            "status": binding.monetary_cost_status,
        },
    }


def _opaque_identity(prefix: str, key: bytes, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _operation_command(provider_id: str, operation: str) -> tuple[str, ...] | None:
    commands = {
        "codex-cli": {
            "start": ("login",),
            "status": ("login", "status"),
            "logout": ("logout",),
        },
        "claude-code": {
            "start": ("auth", "login"),
            "status": ("auth", "status"),
            "logout": ("auth", "logout"),
        },
        "gemini-cli": {
            "start": ("--acp",),
            "status": None,
            "logout": None,
        },
    }
    return commands[provider_id][operation]


def _source(provider_id: str, operation: str) -> str:
    command = _operation_command(provider_id, operation)
    if command is None:
        return "reviewed_provider_authentication_evidence_v1"
    executable = {
        "codex-cli": "codex",
        "claude-code": "claude",
        "gemini-cli": "gemini",
    }[provider_id]
    return " ".join((executable, *command))


def _classify_status(
    provider_id: str,
    result: AuthenticationCommandResult,
) -> tuple[ProviderAccountStatus, str | None, str | None, str | None, str]:
    if result.cancelled:
        return ProviderAccountStatus.UNKNOWN, None, None, None, "status_cancelled"
    if result.timed_out:
        return ProviderAccountStatus.UNKNOWN, None, None, None, "status_timed_out"
    if provider_id == "codex-cli":
        if result.returncode == 0:
            method = _codex_auth_method(result.stdout)
            return ProviderAccountStatus.READY, None, method, None, "provider_ready"
        return ProviderAccountStatus.LOGGED_OUT, None, None, None, "provider_logged_out"
    if provider_id == "claude-code":
        return _classify_claude_status(result)
    return ProviderAccountStatus.UNKNOWN, None, None, None, "machine_status_unavailable"


def _classify_claude_status(
    result: AuthenticationCommandResult,
) -> tuple[ProviderAccountStatus, str | None, str | None, str | None, str]:
    if result.returncode not in {0, 1}:
        return ProviderAccountStatus.UNKNOWN, None, None, None, "status_failed"
    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        if result.returncode == 1:
            return (
                ProviderAccountStatus.LOGGED_OUT,
                None,
                None,
                None,
                "provider_logged_out",
            )
        return ProviderAccountStatus.UNKNOWN, None, None, None, "status_malformed"
    if not isinstance(payload, Mapping):
        return ProviderAccountStatus.UNKNOWN, None, None, None, "status_malformed"
    status_value = str(payload.get("status") or "").strip().lower()
    if status_value in {
        ProviderAccountStatus.EXPIRED.value,
        ProviderAccountStatus.REVOKED.value,
    }:
        status = ProviderAccountStatus(status_value)
    elif payload.get("loggedIn") is True and result.returncode == 0:
        status = ProviderAccountStatus.READY
    elif payload.get("loggedIn") is False or result.returncode == 1:
        status = ProviderAccountStatus.LOGGED_OUT
    else:
        status = ProviderAccountStatus.UNKNOWN
    identity = _first_text(payload, "email", "account", "organizationName")
    method = _first_text(payload, "authMethod", "auth_method", "credentialSource")
    if method is not None and method.lower() in {"none", "unknown"}:
        method = None
    expiry = _first_text(payload, "expiresAt", "expires_at")
    return status, identity, method, expiry, _reason_for_status(status)


def _codex_auth_method(output: bytes) -> str | None:
    text = output.decode("utf-8", errors="replace").lower()
    if "api key" in text:
        return "api_key"
    if "chatgpt" in text:
        return "chatgpt"
    if "access token" in text:
        return "access_token"
    return "provider_reported"


def _first_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _reason_for_status(status: ProviderAccountStatus) -> str:
    return {
        ProviderAccountStatus.READY: "provider_ready",
        ProviderAccountStatus.LOGGED_OUT: "provider_logged_out",
        ProviderAccountStatus.EXPIRED: "provider_credentials_expired",
        ProviderAccountStatus.REVOKED: "provider_credentials_revoked",
        ProviderAccountStatus.UNKNOWN: "provider_status_unknown",
        ProviderAccountStatus.PENDING: "provider_login_pending",
        ProviderAccountStatus.UNAVAILABLE: "provider_unavailable",
    }[status]


def _finished_attempt(
    snapshot: ProviderAccountSnapshot,
    *,
    status: ProviderAccountStatus,
    reason_code: str,
    recovery: tuple[str, ...],
) -> ProviderAccountSnapshot:
    return replace(
        snapshot,
        status=status,
        checked_at=_utc_now(),
        reason_code=reason_code,
        recovery=recovery,
        actions={**snapshot.actions, "cancel": False},
    )


def _expiry(*, expiry: str | None) -> str | None:
    if expiry is None or not _EXPIRY_PATTERN.fullmatch(expiry):
        return None
    try:
        datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return None
    return expiry


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = "".join(character for character in value if ord(character) >= 32).strip()
    return text[:200] or None


def _validated_argument(value: str) -> str:
    text = str(value)
    if not text or "\x00" in text:
        raise ValueError("authentication command contains an invalid argument")
    return text


def _drain_output(stream: Any, output: bytearray) -> None:
    try:
        while chunk := stream.read(4096):
            if len(output) < AUTH_OUTPUT_BYTES:
                remaining = AUTH_OUTPUT_BYTES - len(output)
                output.extend(chunk[:remaining])
    finally:
        stream.close()


def _stop_process(process: subprocess.Popen[Any]) -> None:
    with suppress(OSError, ProcessLookupError):
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()


def _kill_process(process: subprocess.Popen[Any]) -> None:
    with suppress(OSError, ProcessLookupError):
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "AUTH_COMMAND_TIMEOUT_SECONDS",
    "AUTH_OUTPUT_BYTES",
    "AUTH_STATUS_TIMEOUT_SECONDS",
    "AuthenticationCommandResult",
    "BoundedAuthenticationCommandRunner",
    "NativeLoginBroker",
    "ProviderAccountSnapshot",
    "ProviderAccountStatus",
    "ProviderSessionBinding",
    "ProviderAuthenticationBrokerError",
    "ProviderAuthenticationConflictError",
    "ProviderAuthenticationOperationError",
    "provider_account_snapshot_to_dict",
    "provider_session_binding_to_dict",
]


from .login import NativeLoginBroker  # noqa: E402, F401
