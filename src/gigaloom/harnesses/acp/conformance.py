"""Content-free, non-persisting compatibility probe for admitted ACP agents."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from gigaloom.harnesses.acp.client import create_acp_client
from gigaloom.harnesses.acp.contracts import AcpLimits, AcpRouteIdentity
from gigaloom.harnesses.acp.process import pin_acp_process


@dataclass(frozen=True, slots=True)
class AcpProbeReceiptV1:
    """Content-free initialize-only compatibility evidence."""

    state: str
    protocol_version: str
    capability_snapshot_digest: str
    process_fingerprint: str
    compatibility_profile_digest: str
    auth_methods_present: bool
    session_created: bool
    prompt_sent: bool
    native_home_isolated: bool
    network_policy: str
    receipt_digest: str


def run_non_persisting_probe(
    command: tuple[str, ...],
    *,
    route_identity: AcpRouteIdentity,
    limits: AcpLimits | None = None,
    network_isolated: bool,
) -> AcpProbeReceiptV1:
    """Initialize in disposable roots and never create a session or send a prompt.

    ``network_isolated`` is an admission proof supplied by the owning managed
    launcher; the probe refuses to run when that launcher did not establish a
    network-deny boundary.
    """
    if not network_isolated:
        raise ValueError("ACP compatibility probe requires enforced network isolation")
    resolved_limits = limits or AcpLimits()
    with tempfile.TemporaryDirectory(prefix="gigaloom-acp-probe-") as root:
        probe_root = Path(root)
        workspace = probe_root / "workspace"
        native_home = probe_root / "home"
        workspace.mkdir()
        native_home.mkdir()
        spec = pin_acp_process(
            command,
            cwd=workspace,
            environment={
                "HOME": native_home.as_posix(),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": root,
            },
            allowed_environment=frozenset({"HOME"}),
        )
        client = create_acp_client(
            spec,
            compatibility_profile_digest=route_identity.profile_digest,
            route_identity=route_identity,
            limits=resolved_limits,
        )
        try:
            client.start()
            snapshot = client.initialize()
        finally:
            client.close()
        auth_present = bool(snapshot.auth_capabilities.get("methods"))
        payload = {
            "state": "auth_required" if auth_present else "ready",
            "protocol_version": snapshot.protocol_version,
            "capability_snapshot_digest": snapshot.snapshot_digest,
            "process_fingerprint": snapshot.process_fingerprint,
            "compatibility_profile_digest": route_identity.profile_digest,
            "auth_methods_present": auth_present,
            "session_created": False,
            "prompt_sent": False,
            "native_home_isolated": True,
            "network_policy": "enforced_deny",
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return AcpProbeReceiptV1(**payload, receipt_digest=digest)
