"""Worker capability fingerprinting for safe durable job claims."""

from __future__ import annotations

from importlib import metadata
import platform
from typing import Any

from gigaloom.cli_capabilities import cli_capability_snapshot_to_dict
from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.capabilities import negotiate_execution_capabilities
from gigaloom.runtime.structured import DurableStructuredHarness
from gigaloom.types import AvailabilityStatus

_CLI_BINARIES = {
    "codex-cli": "codex",
    "claude-code": "claude",
    "gemini-cli": "gemini",
}


def build_worker_fingerprint(registry: HarnessRegistry) -> dict[str, Any]:
    """Return a redaction-safe snapshot used for claim compatibility."""
    return {
        "os": platform.system().lower(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "gpt2giga": _distribution_version("gpt2giga"),
        "gigaloom": _distribution_version("gigaloom"),
        "harnesses": _harness_fingerprints(registry.list()),
    }


def build_submission_fingerprint(
    registry: HarnessRegistry, harness_id: str
) -> dict[str, Any]:
    """Return only the worker fields required to claim one harness job."""
    return {
        "os": platform.system().lower(),
        "harnesses": _harness_fingerprints((registry.get(harness_id),)),
    }


def _harness_fingerprints(registered: tuple[Any, ...]) -> dict[str, Any]:
    harnesses: dict[str, Any] = {}
    for harness in registered:
        spec = harness.spec()
        availability = harness.availability()
        capabilities = negotiate_execution_capabilities(harness)
        binary = _CLI_BINARIES.get(spec.id)
        resolution = _executable_resolution(harness)
        binary_path = resolution.executable if resolution is not None else None
        probe = _capability_probe(harness)
        profile_features = _agent_profile_features(spec.id, probe)
        structured_features = _structured_features(harness)
        harnesses[spec.id] = {
            "available": availability.status is AvailabilityStatus.AVAILABLE,
            "kind": spec.kind,
            "distribution": str(spec.metadata.get("distribution") or "builtin"),
            "binary": binary,
            "binary_path": binary_path,
            "binary_source": resolution.source if resolution is not None else None,
            "binary_version": probe.version if probe is not None else None,
            "compatibility": (
                cli_capability_snapshot_to_dict(probe) if probe is not None else None
            ),
            "structured_capability_hash": _structured_capability_hash(harness),
            "features": {
                "structured_events": capabilities.structured_events,
                "streaming": capabilities.streaming,
                "cancellation": capabilities.cancellation,
                "synchronous_fallback": capabilities.synchronous_fallback,
                **profile_features,
                **structured_features,
            },
        }
    return harnesses


def _executable_resolution(harness: Any) -> Any | None:
    resolver = getattr(harness, "executable_resolution", None)
    return resolver() if callable(resolver) else None


def _capability_probe(harness: Any) -> Any | None:
    probe = getattr(harness, "capability_probe", None)
    return probe() if callable(probe) else None


def _agent_profile_features(harness_id: str, probe: Any | None) -> dict[str, bool]:
    capabilities = getattr(probe, "capabilities", {})
    if not isinstance(capabilities, dict):
        capabilities = dict(capabilities) if capabilities is not None else {}
    return {
        "agent_reasoning_effort": bool(
            capabilities.get("--config" if harness_id == "codex-cli" else "--effort")
            if harness_id in {"codex-cli", "claude-code"}
            else False
        ),
        "agent_allowed_tools": bool(
            harness_id == "claude-code" and capabilities.get("--allowedTools")
        ),
        "agent_disallowed_tools": bool(
            harness_id == "claude-code" and capabilities.get("--disallowedTools")
        ),
    }


def _structured_features(harness: Any) -> dict[str, bool]:
    if not isinstance(harness, DurableStructuredHarness):
        return {"native_structured": False}
    try:
        capabilities = harness.durable_structured_capabilities()
    except Exception:
        return {"native_structured": False}
    return {
        "native_structured": bool(
            capabilities.structured_events
            and capabilities.interrupt
            and capabilities.resume
            and capabilities.recovery_after_process_loss
            and (capabilities.live_approvals or capabilities.durable_approval)
        )
    }


def _structured_capability_hash(harness: Any) -> str | None:
    if not isinstance(harness, DurableStructuredHarness):
        return None
    try:
        return harness.durable_structured_capabilities().snapshot_hash
    except Exception:
        return None


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "source-checkout"
