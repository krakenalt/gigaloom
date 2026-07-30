"""Exact-evidence compatibility probe for native Codex app-server behavior."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, Mapping

from gigaloom.native.codex_operator.contracts import (
    CodexCapabilityState,
    CodexCompatibilitySnapshot,
)


CODEX_MINIMUM_VERSION = "0.144.5"
CODEX_MAXIMUM_VERSION_EXCLUSIVE = "0.145.0"
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
CODEX_PROTOCOL_MARKERS = (
    '"thread/start"',
    '"thread/resume"',
    '"thread/compact/start"',
    '"contextCompaction"',
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


def probe_codex_compatibility(
    command: tuple[str, ...],
    *,
    run: _Run | None = None,
    generate_schema: _GenerateSchema | None = None,
) -> CodexCompatibilitySnapshot:
    """Probe Codex without reading or mutating the user's native home."""
    if not command:
        return _snapshot(
            status=CodexCapabilityState.UNSUPPORTED,
            reason_code="executable_missing",
        )
    run_probe = run or _run
    schema_probe = generate_schema or _generate_schema
    with tempfile.TemporaryDirectory(prefix="gigaloom-codex-probe-") as raw_home:
        env = dict(os.environ)
        env["CODEX_HOME"] = str(Path(raw_home) / "home")
        version_result = run_probe((*command, "--version"), env)
        if version_result[0] != 0:
            return _snapshot(
                status=CodexCapabilityState.UNSUPPORTED,
                reason_code="version_probe_failed",
            )
        version_output = _first_line(version_result[1])
        parsed_version = _parsed_version(version_output)
        if parsed_version is None:
            return _native_only(
                version_output=version_output,
                parsed_version=None,
                reason_code="version_unparsed",
            )
        if not _version_in_window(parsed_version):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="version_outside_window",
            )
        tui_result = run_probe((*command, "--help"), env)
        app_server_result = run_probe((*command, "app-server", "--help"), env)
        if tui_result[0] != 0 or not _contains_all(
            tui_result[1], CODEX_TUI_HELP_MARKERS
        ):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="remote_tui_contract_missing",
            )
        if app_server_result[0] != 0 or not _contains_all(
            app_server_result[1], CODEX_APP_SERVER_HELP_MARKERS
        ):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="app_server_contract_missing",
            )
        try:
            bundle_digest, file_digests = schema_probe(command, env)
        except (OSError, subprocess.SubprocessError, ValueError):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="schema_probe_failed",
            )
        if bundle_digest != CODEX_SCHEMA_BUNDLE_SHA256:
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="schema_digest_mismatch",
                schema_bundle_sha256=bundle_digest,
            )
        if any(
            file_digests.get(path) != digest
            for path, digest in CODEX_REQUIRED_SCHEMA_DIGESTS.items()
        ):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="required_schema_mismatch",
                schema_bundle_sha256=bundle_digest,
            )
        protocol_path = "codex_app_server_protocol.v2.schemas.json"
        protocol_text = file_digests.get(f"{protocol_path}:text", "")
        if not _contains_all(protocol_text, CODEX_PROTOCOL_MARKERS):
            return _native_only(
                version_output=version_output,
                parsed_version=parsed_version,
                reason_code="protocol_method_missing",
                schema_bundle_sha256=bundle_digest,
            )
        return _snapshot(
            status=CodexCapabilityState.SUPPORTED,
            version_output=version_output,
            parsed_version=parsed_version,
            reason_code="exact_evidence_admitted",
            schema_bundle_sha256=bundle_digest,
            transport="unix",
        )


def _native_only(
    *,
    version_output: str,
    parsed_version: str | None,
    reason_code: str,
    schema_bundle_sha256: str | None = None,
) -> CodexCompatibilitySnapshot:
    return _snapshot(
        status=CodexCapabilityState.NATIVE_ONLY,
        version_output=version_output,
        parsed_version=parsed_version,
        reason_code=reason_code,
        schema_bundle_sha256=schema_bundle_sha256,
    )


def _snapshot(
    *,
    status: CodexCapabilityState,
    reason_code: str,
    version_output: str | None = None,
    parsed_version: str | None = None,
    schema_bundle_sha256: str | None = None,
    transport: str | None = None,
) -> CodexCompatibilitySnapshot:
    native_state = (
        CodexCapabilityState.UNSUPPORTED
        if status is CodexCapabilityState.UNSUPPORTED
        else CodexCapabilityState.SUPPORTED
    )
    structured_state = (
        CodexCapabilityState.SUPPORTED
        if status is CodexCapabilityState.SUPPORTED
        else status
    )
    return CodexCompatibilitySnapshot(
        status=status,
        executable_version=version_output,
        parsed_version=parsed_version,
        minimum_version=CODEX_MINIMUM_VERSION,
        maximum_version_exclusive=CODEX_MAXIMUM_VERSION_EXCLUSIVE,
        schema_bundle_sha256=schema_bundle_sha256,
        capabilities={
            name: (native_state if name == "native_tui" else structured_state)
            for name in _CAPABILITY_NAMES
        },
        transport=transport,
        reason_code=reason_code,
    )


def _generate_schema(
    command: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[str, Mapping[str, str]]:
    with tempfile.TemporaryDirectory(prefix="gigaloom-codex-schema-") as raw_dir:
        root = Path(raw_dir)
        completed = subprocess.run(
            (*command, "app-server", "generate-json-schema", "--out", str(root)),
            check=False,
            capture_output=True,
            text=True,
            timeout=15.0,
            env=dict(env),
        )
        if completed.returncode != 0:
            raise ValueError("Codex schema generation failed")
        files = sorted(path for path in root.rglob("*") if path.is_file())
        digest = hashlib.sha256()
        file_digests: dict[str, str] = {}
        for path in files:
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            canonical_content = json.dumps(
                json.loads(content),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(canonical_content)
            file_digests[relative] = hashlib.sha256(canonical_content).hexdigest()
        protocol_path = root / "codex_app_server_protocol.v2.schemas.json"
        if protocol_path.is_file():
            file_digests["codex_app_server_protocol.v2.schemas.json:text"] = (
                protocol_path.read_text(encoding="utf-8")
            )
        return digest.hexdigest(), file_digests


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
