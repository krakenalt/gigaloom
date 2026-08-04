"""Reviewed production profile and installed-artifact evidence for gpt2giga."""

from __future__ import annotations

import base64
import hashlib
from importlib.metadata import PackageNotFoundError, distribution
import os
from pathlib import Path
import shutil
import sys

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.native.launch.gateway_contracts import (
    GatewayMode,
    GatewayProfileV1,
    gateway_version_admitted,
)
from gigaloom.native.launch.gateway_sidecar import GatewayArtifactEvidenceV1


GPT2GIGA_DISTRIBUTION = "gpt2giga"
GPT2GIGA_EXECUTABLE = "gpt2giga"
GPT2GIGA_VERSION = "0.3.0"
GPT2GIGA_VERSION_WINDOW = ">=0.3.0,<0.4.0"
GPT2GIGA_WHEEL_SHA256 = (
    "8156aa7b624d619fb657bb1c6530e9e65e74da31324291ff6f156112b325483f"
)
GPT2GIGA_STARTUP_CONFIG_REVISION = (
    "sha256:01332070f29b6b30b799ccfe65d9435d137984467cd9607b0ce83a373c5ecd1f"
)
GPT2GIGA_HEALTH_CONTRACT_REVISION = "gpt2giga.health.v1"
GPT2GIGA_READINESS_CONTRACT_REVISION = "gpt2giga.readiness.v1"
GPT2GIGA_MODELS_CONTRACT_REVISION = "openai.models.v1"
GPT2GIGA_CAPABILITIES_CONTRACT_REVISION = "gpt2giga.route-support-matrix.v1"
GPT2GIGA_INSPECT_CONTRACT_REVISION = "gpt2giga.inspect.v1"
GPT2GIGA_PROVIDER_PROFILE_REVISION = "gpt2giga.provider-profiles.v2"
GPT2GIGA_LOSS_MATRIX_REVISION = (
    "sha256:3cad19e6f7b531e50a0eb4c88af308aee5be81f3a4c085f89ebc2e06f92ffa69"
)


def reviewed_gpt2giga_profile(
    *,
    base_url: str,
    mode: GatewayMode,
) -> GatewayProfileV1:
    """Build the exact reviewed 0.3.0 profile from the public handoff."""
    semantic = {
        "gateway_id": GPT2GIGA_DISTRIBUTION,
        "display_name": "gpt2giga 0.3",
        "mode": mode.value,
        "distribution": GPT2GIGA_DISTRIBUTION,
        "executable": GPT2GIGA_EXECUTABLE,
        "version": GPT2GIGA_VERSION,
        "version_window": GPT2GIGA_VERSION_WINDOW,
        "artifact_sha256": GPT2GIGA_WHEEL_SHA256,
        "base_url": base_url,
        "startup_config_revision": GPT2GIGA_STARTUP_CONFIG_REVISION,
        "health_contract_revision": GPT2GIGA_HEALTH_CONTRACT_REVISION,
        "readiness_contract_revision": GPT2GIGA_READINESS_CONTRACT_REVISION,
        "models_contract_revision": GPT2GIGA_MODELS_CONTRACT_REVISION,
        "capabilities_contract_revision": (GPT2GIGA_CAPABILITIES_CONTRACT_REVISION),
        "auth_ref": "secret-ref:gpt2giga-api-key",
        "tls_policy_ref": (
            "tls-policy:loopback"
            if mode is GatewayMode.MANAGED
            else "tls-policy:configured"
        ),
    }
    return GatewayProfileV1(
        gateway_id=GPT2GIGA_DISTRIBUTION,
        display_name="gpt2giga 0.3",
        mode=mode,
        distribution=GPT2GIGA_DISTRIBUTION,
        executable=GPT2GIGA_EXECUTABLE,
        version=GPT2GIGA_VERSION,
        version_window=GPT2GIGA_VERSION_WINDOW,
        artifact_sha256=GPT2GIGA_WHEEL_SHA256,
        base_url=base_url,
        startup_config_revision=GPT2GIGA_STARTUP_CONFIG_REVISION,
        health_contract_revision=GPT2GIGA_HEALTH_CONTRACT_REVISION,
        readiness_contract_revision=GPT2GIGA_READINESS_CONTRACT_REVISION,
        models_contract_revision=GPT2GIGA_MODELS_CONTRACT_REVISION,
        capabilities_contract_revision=GPT2GIGA_CAPABILITIES_CONTRACT_REVISION,
        auth_ref="secret-ref:gpt2giga-api-key",
        tls_policy_ref=semantic["tls_policy_ref"],
        profile_digest=canonical_digest(semantic),
    )


def resolve_installed_gpt2giga_artifact(
    profile: GatewayProfileV1,
) -> GatewayArtifactEvidenceV1 | None:
    """Verify the installed public distribution against its registry receipt."""
    if profile.distribution != GPT2GIGA_DISTRIBUTION:
        return None
    try:
        installed = distribution(GPT2GIGA_DISTRIBUTION)
    except PackageNotFoundError:
        return None
    executable = _installed_executable(profile.executable)
    scripts = {
        entry.name: entry.value
        for entry in installed.entry_points
        if entry.group == "console_scripts"
    }
    observed_digest = _verified_record_digest(installed)
    reason_id: str | None = None
    if not gateway_version_admitted(installed.version, profile.version_window):
        reason_id = "gateway_version_outside_supported_window"
    elif installed.read_text("direct_url.json") is not None:
        reason_id = "gateway_package_provenance_unverified"
    elif scripts != {GPT2GIGA_EXECUTABLE: "gpt2giga:run"}:
        reason_id = "gateway_executable_contract_mismatch"
    elif executable is None:
        reason_id = "gateway_executable_unavailable"
    elif observed_digest is None:
        reason_id = "gateway_package_record_invalid"
    return GatewayArtifactEvidenceV1(
        distribution=installed.metadata["Name"] or GPT2GIGA_DISTRIBUTION,
        version=installed.version,
        artifact_sha256=observed_digest or "0" * 64,
        executable_path=os.fspath(executable) if executable is not None else "",
        source=f"registry:pypi/gpt2giga=={installed.version}",
        verified=reason_id is None,
        reason_id=reason_id,
    )


def _installed_executable(name: str) -> Path | None:
    candidates = (Path(sys.executable).parent / name, shutil.which(name))
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def _verified_record_digest(installed: object) -> str | None:
    files = getattr(installed, "files", None)
    locate = getattr(installed, "locate_file", None)
    if not files or not callable(locate):
        return None
    records: list[tuple[str, str]] = []
    for item in files:
        recorded = getattr(item, "hash", None)
        if recorded is None:
            continue
        try:
            data = Path(locate(item)).read_bytes()
            digest = hashlib.new(recorded.mode, data).digest()
        except (OSError, ValueError):
            return None
        actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        if actual != recorded.value:
            return None
        records.append((recorded.mode, recorded.value))
    if not records:
        return None
    return canonical_digest(sorted(records))


__all__ = [
    "GPT2GIGA_CAPABILITIES_CONTRACT_REVISION",
    "GPT2GIGA_DISTRIBUTION",
    "GPT2GIGA_INSPECT_CONTRACT_REVISION",
    "GPT2GIGA_LOSS_MATRIX_REVISION",
    "GPT2GIGA_PROVIDER_PROFILE_REVISION",
    "GPT2GIGA_STARTUP_CONFIG_REVISION",
    "GPT2GIGA_VERSION",
    "GPT2GIGA_WHEEL_SHA256",
    "resolve_installed_gpt2giga_artifact",
    "reviewed_gpt2giga_profile",
]
