"""Exact-evidence compatibility probe for native Codex app-server behavior."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Mapping

from gigaloom.contracts.compatibility import (
    CapabilityAdmissionV1,
    CompatibilityObservationV1,
    CompatibilityStatus,
    ExecutableObservationV1,
    KnownIncompatibilityV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    SecurityCompatibilityV1,
    evaluate_compatibility,
)
from gigaloom.contracts.compatibility_fingerprints import (
    CompatibilityProbeCacheKeyV1,
    digest_command_tokens,
)
from gigaloom.native.codex_operator.contracts import (
    CodexCapabilityState,
    CodexCompatibilitySnapshot,
)
from gigaloom.native.codex_operator import compatibility_probe as _probe_support


CODEX_MINIMUM_VERSION = "0.144.5"
CODEX_MAXIMUM_VERSION_EXCLUSIVE = "0.145.0"
CODEX_COMPATIBILITY_PROFILE_DIGEST = (
    "c0088f2282929e7cde079c32c083ffc6fb022a0880fadea6d57deb01953d54cb"
)
CODEX_SCHEMA_BUNDLE_SHA256 = (
    "a1a35476587fe9bbfbe9e291b5200b8bc541df8c00241fe578d285ff26996e1c"
)
CODEX_REQUIRED_SCHEMA_DIGESTS: Mapping[str, str] = {
    "codex_app_server_protocol.v2.schemas.json": (
        "5d8251e1e2f713a3c567c927386f84f2f94692d4721b90d8ff36d0ff92877621"
    ),
    "v2/ItemCompletedNotification.json": (
        "105dd05e08e3e4105a0eb0ae0cb68520270e7dc140879d4750cce53f2f822a95"
    ),
    "v2/ItemStartedNotification.json": (
        "6fdaebce3baccc963e51197722f2ce7b0ec611c8c7d2b3ee018e85cb4d2b5274"
    ),
    "v2/ThreadCompactStartParams.json": (
        "0dff617cb93a1398149de30d6b53098dabca86ba1ff750a7b2af8023651b9dfc"
    ),
    "v2/ThreadCompactStartResponse.json": (
        "3933ea992c2fa391f8762b71c27133d4bf2e10c6fb84459bcbe05169af23cbc0"
    ),
    "v2/ThreadResumeParams.json": (
        "d867460ca8c348a8652ea0578ccaa4d67568c2bb462c1c71cd13ac4dcf91dc1b"
    ),
    "v2/ThreadStartParams.json": (
        "d960450fe2d0c1bf65f5aad42b070faefd59973ea9645d021b011ebdf23b5c03"
    ),
}
CODEX_TUI_HELP_MARKERS = ("--remote <ADDR>", "unix://PATH", "ws://host:port")
CODEX_APP_SERVER_HELP_MARKERS = (
    "generate-json-schema",
    "--listen <URL>",
    "stdio://",
    "unix://PATH",
)
CODEX_PROTOCOL_FAMILY = "codex_app_server"
CODEX_PROTOCOL_MAJOR = "2"
CODEX_OBSERVATION_TTL_SECONDS = 3600
CODEX_REQUIRED_CAPABILITY_MARKERS: Mapping[str, str] = {
    "structured_mirror": '"thread/start"',
    "thread_resume": '"thread/resume"',
    "thread_compact": '"thread/compact/start"',
    "context_compaction_items": '"contextCompaction"',
}
CODEX_REQUIRED_CAPABILITIES = (
    "context_compaction_items",
    "remote_tui",
    "structured_mirror",
    "thread_compact",
    "thread_resume",
)
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
_CAPABILITY_NAMES = (
    "native_tui",
    "remote_tui",
    "structured_mirror",
    "thread_resume",
    "thread_compact",
    "context_compaction_items",
)
_Run = Callable[[tuple[str, ...], Mapping[str, str]], tuple[int, str]]
_GenerateSchema = Callable[
    [tuple[str, ...], Mapping[str, str]], tuple[str, Mapping[str, str]]
]


CodexSchemaProbeUnavailable = _probe_support.CodexSchemaProbeUnavailable
CodexProtocolFramingError = _probe_support.CodexProtocolFramingError


def probe_codex_compatibility(
    command: tuple[str, ...],
    *,
    run: _Run | None = None,
    generate_schema: _GenerateSchema | None = None,
    allow_compatible_unverified: bool = True,
    profile_digest: str = CODEX_COMPATIBILITY_PROFILE_DIGEST,
    known_incompatibilities: tuple[KnownIncompatibilityV1, ...] = (),
    platform: str | None = None,
    now: datetime | None = None,
    observation_ttl_seconds: int = CODEX_OBSERVATION_TTL_SECONDS,
) -> CodexCompatibilitySnapshot:
    """Probe Codex conformance without reading or mutating its native home."""
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("compatibility probe time must be timezone-aware")
    if (
        isinstance(observation_ttl_seconds, bool)
        or observation_ttl_seconds <= 0
        or observation_ttl_seconds > 86_400
    ):
        raise ValueError("compatibility observation TTL is invalid")
    expires_at = observed_at + timedelta(seconds=observation_ttl_seconds)
    platform_id = platform or sys.platform
    if not command:
        protocol = _protocol_observation(
            state=ProtocolNegotiationState.UNAVAILABLE,
            version=None,
            bundle_digest=None,
            file_digests={},
            protocol_text="",
            app_server_help="",
        )
        observation = _build_observation(
            command=command,
            profile_digest=profile_digest,
            platform=platform_id,
            executable_observed=False,
            version_output=None,
            parsed_version=None,
            exact_evidence_matched=False,
            protocol=protocol,
            observed_capabilities=(),
            security_failures=(),
            known_incompatibilities=(),
            observed_at=observed_at,
            expires_at=expires_at,
        )
        return _snapshot_from_observation(
            observation,
            version_output=None,
            schema_bundle_sha256=None,
            allow_compatible_unverified=allow_compatible_unverified,
        )
    run_probe = run or _run
    schema_probe = generate_schema or _generate_schema
    with tempfile.TemporaryDirectory(prefix="gigaloom-codex-probe-") as raw_home:
        env = {
            key: value
            for key in ("PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
            if (value := os.environ.get(key)) is not None
        }
        env["HOME"] = raw_home
        env["CODEX_HOME"] = str(Path(raw_home) / "home")
        version_result = run_probe((*command, "--version"), env)
        tui_result = run_probe((*command, "--help"), env)
        app_server_result = run_probe((*command, "app-server", "--help"), env)
        version_output = (
            _first_line(version_result[1]) if version_result[0] == 0 else ""
        )
        parsed_version = _parsed_version(version_output)
        executable_observed = _executable_observed(
            command,
            results=(version_result, tui_result, app_server_result),
        )
        bundle_digest: str | None = None
        file_digests: Mapping[str, str] = {}
        protocol_text = ""
        protocol_version: str | None = None
        protocol_state = ProtocolNegotiationState.UNAVAILABLE
        security_failures: tuple[str, ...] = ()
        app_server_available = app_server_result[0] == 0 and _contains_all(
            app_server_result[1], CODEX_APP_SERVER_HELP_MARKERS
        )
        if app_server_available:
            try:
                raw_bundle_digest, raw_file_digests = schema_probe(command, env)
                (
                    bundle_digest,
                    file_digests,
                    protocol_version,
                    protocol_text,
                ) = _inspect_schema_evidence(
                    raw_bundle_digest,
                    raw_file_digests,
                )
                protocol_state = (
                    ProtocolNegotiationState.CONFORMANT
                    if protocol_version == CODEX_PROTOCOL_MAJOR
                    else ProtocolNegotiationState.MAJOR_MISMATCH
                )
            except CodexSchemaProbeUnavailable:
                protocol_state = ProtocolNegotiationState.UNAVAILABLE
            except (
                CodexProtocolFramingError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                protocol_state = ProtocolNegotiationState.MALFORMED
                security_failures = ("malformed_protocol_framing",)
            except (OSError, subprocess.SubprocessError):
                protocol_state = ProtocolNegotiationState.UNAVAILABLE
        protocol = _protocol_observation(
            state=protocol_state,
            version=protocol_version,
            bundle_digest=bundle_digest,
            file_digests=file_digests,
            protocol_text=protocol_text,
            app_server_help=app_server_result[1],
        )
        observed_capabilities = _observed_capabilities(
            executable_observed=executable_observed,
            tui_help=tui_result[1] if tui_result[0] == 0 else "",
            protocol_text=protocol_text,
        )
        exact_evidence_matched = (
            parsed_version is not None
            and _version_in_window(parsed_version)
            and bundle_digest == CODEX_SCHEMA_BUNDLE_SHA256
            and all(
                file_digests.get(path) == digest
                for path, digest in CODEX_REQUIRED_SCHEMA_DIGESTS.items()
            )
        )
        observation = _build_observation(
            command=command,
            profile_digest=profile_digest,
            platform=platform_id,
            executable_observed=executable_observed,
            version_output=version_output or None,
            parsed_version=parsed_version,
            exact_evidence_matched=exact_evidence_matched,
            protocol=protocol,
            observed_capabilities=observed_capabilities,
            security_failures=security_failures,
            known_incompatibilities=(
                known_incompatibilities if executable_observed else ()
            ),
            observed_at=observed_at,
            expires_at=expires_at,
        )
        return _snapshot_from_observation(
            observation,
            version_output=version_output or None,
            schema_bundle_sha256=bundle_digest,
            allow_compatible_unverified=allow_compatible_unverified,
        )


def _build_observation(
    *,
    command: tuple[str, ...],
    profile_digest: str,
    platform: str,
    executable_observed: bool,
    version_output: str | None,
    parsed_version: str | None,
    exact_evidence_matched: bool,
    protocol: ProtocolNegotiationV1,
    observed_capabilities: tuple[str, ...],
    security_failures: tuple[str, ...],
    known_incompatibilities: tuple[KnownIncompatibilityV1, ...],
    observed_at: datetime,
    expires_at: datetime,
) -> CompatibilityObservationV1:
    reviewed_state = ReviewedVersionState.UNKNOWN
    if parsed_version is not None:
        reviewed_state = (
            ReviewedVersionState.IN_RANGE
            if _version_in_window(parsed_version)
            else ReviewedVersionState.OUTSIDE_RANGE
        )
    observed = tuple(sorted(set(observed_capabilities)))
    missing = tuple(sorted(set(CODEX_REQUIRED_CAPABILITIES) - set(observed)))
    capability_fingerprint = _canonical_digest({"capabilities": observed})
    executable_identity = _executable_identity(
        command,
        version_output=version_output,
        observed=executable_observed,
    )
    cache_key = CompatibilityProbeCacheKeyV1(
        executable_identity=executable_identity,
        profile_digest=profile_digest,
        command_tokens_digest=digest_command_tokens(command),
        protocol_handshake_digest=protocol.handshake_digest,
        platform=platform,
    )
    return evaluate_compatibility(
        agent_id="codex",
        route_id="structured_native",
        profile_digest=profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=executable_identity,
            reported_version=parsed_version,
            observed=executable_observed,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=reviewed_state,
            evidence_digest=profile_digest,
            exact_evidence_matched=exact_evidence_matched,
        ),
        protocol=protocol,
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=capability_fingerprint,
            required_capabilities=CODEX_REQUIRED_CAPABILITIES,
            missing_capabilities=missing,
        ),
        security=SecurityCompatibilityV1(
            invariant_failures=security_failures,
            known_incompatibilities=known_incompatibilities,
        ),
        cache_key=cache_key,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def _snapshot_from_observation(
    observation: CompatibilityObservationV1,
    *,
    version_output: str | None,
    schema_bundle_sha256: str | None,
    allow_compatible_unverified: bool,
) -> CodexCompatibilitySnapshot:
    admitted = observation.structured_admitted(
        allow_unverified=allow_compatible_unverified
    )
    if observation.status is CompatibilityStatus.VERIFIED:
        status = CodexCapabilityState.SUPPORTED
    elif observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED and admitted:
        status = CodexCapabilityState.COMPATIBLE_UNVERIFIED
    elif not observation.native_eligible:
        status = CodexCapabilityState.UNSUPPORTED
    else:
        status = CodexCapabilityState.NATIVE_ONLY
    native_state = (
        CodexCapabilityState.SUPPORTED
        if observation.native_eligible
        else CodexCapabilityState.UNSUPPORTED
    )
    structured_state = status if admitted else CodexCapabilityState.NATIVE_ONLY
    if status is CodexCapabilityState.UNSUPPORTED:
        structured_state = CodexCapabilityState.UNSUPPORTED
    return CodexCompatibilitySnapshot(
        status=status,
        executable_version=version_output,
        parsed_version=observation.reported_version,
        minimum_version=CODEX_MINIMUM_VERSION,
        maximum_version_exclusive=CODEX_MAXIMUM_VERSION_EXCLUSIVE,
        schema_bundle_sha256=schema_bundle_sha256,
        capabilities={
            name: (native_state if name == "native_tui" else structured_state)
            for name in _CAPABILITY_NAMES
        },
        transport="unix" if admitted else None,
        reason_code=_snapshot_reason(
            observation,
            allow_compatible_unverified=allow_compatible_unverified,
        ),
        observation=observation,
    )


def _snapshot_reason(
    observation: CompatibilityObservationV1,
    *,
    allow_compatible_unverified: bool,
) -> str:
    if observation.status is CompatibilityStatus.VERIFIED:
        return "exact_evidence_admitted"
    if observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED:
        return (
            "conformance_admitted_unverified"
            if allow_compatible_unverified
            else "compatible_unverified_policy_blocked"
        )
    if observation.status is CompatibilityStatus.UNSAFE:
        return (
            observation.security.invariant_failures[0]
            if observation.security.invariant_failures
            else "malformed_protocol_framing"
        )
    if observation.status is CompatibilityStatus.INCOMPATIBLE:
        if observation.protocol.state is ProtocolNegotiationState.MAJOR_MISMATCH:
            return "protocol_major_mismatch"
        return observation.known_incompatibilities[0].reason_code
    if observation.status is CompatibilityStatus.UNAVAILABLE:
        return "executable_missing"
    if observation.missing_capabilities:
        return f"missing_mandatory_capability:{observation.missing_capabilities[0]}"
    return "structured_probe_unavailable"


def _protocol_observation(
    *,
    state: ProtocolNegotiationState,
    version: str | None,
    bundle_digest: str | None,
    file_digests: Mapping[str, str],
    protocol_text: str,
    app_server_help: str,
) -> ProtocolNegotiationV1:
    return _probe_support.protocol_observation(
        protocol_family=CODEX_PROTOCOL_FAMILY,
        version=version,
        state=state,
        bundle_digest=bundle_digest,
        file_digests=file_digests,
        protocol_text=protocol_text,
        app_server_help=app_server_help,
    )


def _inspect_schema_evidence(
    bundle_digest: object,
    file_digests: object,
) -> tuple[str, Mapping[str, str], str, str]:
    return _probe_support.inspect_schema_evidence(bundle_digest, file_digests)


def _observed_capabilities(
    *,
    executable_observed: bool,
    tui_help: str,
    protocol_text: str,
) -> tuple[str, ...]:
    return _probe_support.observed_capabilities(
        executable_observed=executable_observed,
        tui_help=tui_help,
        protocol_text=protocol_text,
        tui_help_markers=CODEX_TUI_HELP_MARKERS,
        required_capability_markers=CODEX_REQUIRED_CAPABILITY_MARKERS,
    )


def _executable_observed(
    command: tuple[str, ...],
    *,
    results: tuple[tuple[int, str], ...],
) -> bool:
    return _probe_support.executable_observed(command, results=results)


def _executable_identity(
    command: tuple[str, ...],
    *,
    version_output: str | None,
    observed: bool,
) -> str:
    return _probe_support.executable_identity(
        command,
        version_output=version_output,
        observed=observed,
    )


def _canonical_digest(payload: object) -> str:
    return _probe_support.canonical_digest(payload)


def _generate_schema(
    command: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[str, Mapping[str, str]]:
    return _probe_support.generate_schema(command, env)


def _run(
    command: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return completed.returncode, output[:32_000]


def _first_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "")


def _parsed_version(value: str) -> str | None:
    match = _VERSION_PATTERN.search(value)
    return match.group(1) if match is not None else None


def _version_in_window(value: str) -> bool:
    parsed = tuple(int(part) for part in value.split("."))
    minimum = tuple(int(part) for part in CODEX_MINIMUM_VERSION.split("."))
    maximum = tuple(int(part) for part in CODEX_MAXIMUM_VERSION_EXCLUSIVE.split("."))
    return minimum <= parsed < maximum


def _contains_all(value: str, markers: tuple[str, ...]) -> bool:
    return all(marker in value for marker in markers)
