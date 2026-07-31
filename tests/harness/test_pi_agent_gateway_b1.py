"""B1 acceptance for independent native Pi and generic ACP routes."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import re

import pytest

from gigaloom.harnesses.acp.api import (
    AcpRouteIdentity,
    run_non_persisting_probe,
)
from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpImplementationInfo,
    CapabilityLoss,
    NegotiatedFeature,
)
from gigaloom.harnesses.agent_profiles import (
    AgentProbePlanStatus,
    AgentProfileRegistry,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
    load_local_agent_profile,
    plan_agent_probe,
)
from gigaloom.harnesses.api import (
    CapabilityCatalogFactState,
    CapabilitySnapshotState,
    bind_structured_route_descriptors,
    build_capability_catalog,
    project_acp_capability_snapshot,
)
from gigaloom.native.launch import (
    RegisteredNativeLaunchFailure,
    RegisteredNativeLaunchStatus,
    TerminalContext,
    launch_registered_native_agent,
)
from gigaloom.native_cli_facade import run_native_namespace


ROOT = Path(__file__).parents[2]
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "agent_profiles" / "test-agent.toml"
ACP_FIXTURE = ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"
PIPE = TerminalContext(False, False, True, "xterm-256color", platform="darwin")
COLLISIONS = build_core_command_collision_contract(("agent", "run", "ui"))
_PROVIDER_BRANCH = re.compile(
    r"\b(?:if|elif)\s+[^\n]*[\"']pi[\"']|\bcase\s+[\"']pi[\"']"
)


def _pi_profile():
    return next(
        profile for profile in load_builtin_agent_profiles() if profile.agent_id == "pi"
    )


def _pi_route():
    profile = _pi_profile()
    return profile, profile.structured_routes[0], profile.compatibility_profiles[0]


def _snapshot(*, ready: bool) -> AcpCapabilitySnapshotV1:
    _profile, _route, compatibility = _pi_route()
    return AcpCapabilitySnapshotV1(
        protocol_version="1",
        client_info=AcpImplementationInfo("gigaloom", "GigaLoom", "0.7.0a1"),
        agent_info=AcpImplementationInfo("fixture-agent", None, "1.0.0"),
        agent_capabilities={},
        session_capabilities={},
        auth_capabilities={},
        negotiated_features=(
            (NegotiatedFeature("structured_prompt"),) if ready else ()
        ),
        unsupported_features=(
            ()
            if ready
            else (
                CapabilityLoss(
                    "structured_prompt",
                    "unsupported",
                    "not_advertised",
                ),
            )
        ),
        compatibility_profile_digest=compatibility.evidence_digest,
        process_fingerprint="a" * 64,
        connection_generation=1,
        snapshot_digest=("b" if ready else "c") * 64,
    )


def test_pi_and_temporary_agent_share_dynamic_root_and_exact_suffix(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes(PROFILE_FIXTURE.read_bytes())
    profiles = (*load_builtin_agent_profiles(), load_local_agent_profile(manifest))
    registry = AgentProfileRegistry.build(
        profiles,
        collision_contract=COLLISIONS,
    )
    calls: list[tuple[str, str, tuple[str, ...]]] = []

    def runner(process_spec, suffix, **_kwargs):
        calls.append((process_spec.namespace, process_spec.executable, suffix))
        return 19

    for argv in (
        ("pi", "--future-provider-flag", "opaque value"),
        ("test-agent", "--future-provider-flag", "opaque value"),
    ):
        result = run_native_namespace(
            argv,
            registry=registry,
            context=PIPE,
            runner=runner,
            managed_runner=lambda *_args, **_kwargs: pytest.fail(
                "unknown non-TTY form must stay direct"
            ),
        )
        assert result == 19

    assert calls == [
        ("pi", "pi", ("--future-provider-flag", "opaque value")),
        (
            "test-agent",
            "test-agent",
            ("--future-provider-flag", "opaque value"),
        ),
    ]
    generic_sources = (
        ROOT / "src" / "gigaloom" / "entrypoint.py",
        ROOT / "src" / "gigaloom" / "native_cli_facade.py",
        ROOT / "src" / "gigaloom" / "native" / "launch" / "launcher.py",
        ROOT / "src" / "gigaloom" / "harnesses" / "acp" / "client.py",
    )
    for source_path in generic_sources:
        assert _PROVIDER_BRANCH.search(source_path.read_text(encoding="utf-8")) is None


def test_missing_native_pi_never_falls_back_to_available_pi_acp(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    adapter = bin_dir / "pi-acp"
    adapter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    adapter.chmod(0o755)
    registry = AgentProfileRegistry.build(
        load_builtin_agent_profiles(),
        collision_contract=COLLISIONS,
    )

    result = launch_registered_native_agent(
        ("pi", "--help"),
        registry=registry,
        context=PIPE,
        environment={"PATH": str(bin_dir)},
        direct_runner=lambda *_args: pytest.fail("missing native Pi must not run ACP"),
        managed_runner=lambda *_args: pytest.fail("missing native Pi must not run ACP"),
    )

    assert result == RegisteredNativeLaunchFailure(
        RegisteredNativeLaunchStatus.EXECUTABLE_MISSING,
        requested_token="pi",
        agent_id="pi",
    )


def test_pi_acp_evidence_is_digest_bound_inert_and_probe_safe() -> None:
    profile, route, compatibility = _pi_route()
    resource = files("gigaloom.harnesses.agent_profiles.builtins").joinpath(
        "evidence",
        "pi-acp-0.0.33.json",
    )
    evidence_bytes = resource.read_bytes()
    evidence = json.loads(evidence_bytes)

    assert sha256(evidence_bytes).hexdigest() == compatibility.evidence_digest
    assert route.route_id == "pi.acp"
    assert route.harness_id == "acp-gateway"
    assert route.transport_kind == "acp_stdio_v1"
    assert route.command_ref is not None
    assert (route.command_ref.executable_name, *route.command_ref.arguments) == (
        "pi-acp",
    )
    assert evidence["reviewed_contract"]["automatic_install"] is False
    assert evidence["reviewed_contract"]["npx_fallback"] is False
    assert evidence["reviewed_contract"]["hidden_fallback_route"] is None
    assert "npx" not in (
        route.command_ref.executable_name,
        *route.command_ref.arguments,
    )

    plan = plan_agent_probe(
        profile,
        route_id="pi.acp",
        environment={"PATH": ""},
        platform="darwin",
    )
    assert plan.status is AgentProbePlanStatus.PLANNED
    assert plan.executable_path == "pi-acp"
    assert plan.arguments == ()
    assert plan.execution_performed is False

    receipt = run_non_persisting_probe(
        (ACP_FIXTURE.resolve().as_posix(),),
        route_identity=AcpRouteIdentity(
            profile.agent_id,
            route.route_id,
            compatibility.evidence_digest,
        ),
        network_isolated=True,
    )
    assert receipt.state == "ready"
    assert receipt.session_created is False
    assert receipt.prompt_sent is False
    assert receipt.native_home_isolated is True


def test_pi_acp_readiness_and_loss_are_negotiated_independently() -> None:
    descriptors = bind_structured_route_descriptors(load_builtin_agent_profiles())
    pi_descriptor = next(item for item in descriptors if item.route_id == "pi.acp")
    unknown = build_capability_catalog((pi_descriptor,))
    ready = build_capability_catalog(
        (pi_descriptor,),
        (project_acp_capability_snapshot("pi.acp", _snapshot(ready=True)),),
    )
    degraded = build_capability_catalog(
        (pi_descriptor,),
        (project_acp_capability_snapshot("pi.acp", _snapshot(ready=False)),),
    )

    assert unknown.routes[0].snapshot_state is CapabilitySnapshotState.UNKNOWN
    assert unknown.routes[0].capabilities[0].state is (
        CapabilityCatalogFactState.UNKNOWN
    )
    assert ready.routes[0].snapshot_state is CapabilitySnapshotState.CURRENT
    assert ready.routes[0].capabilities[0].state is CapabilityCatalogFactState.READY
    assert degraded.routes[0].snapshot_state is CapabilitySnapshotState.CURRENT
    assert degraded.routes[0].capabilities[0].state is (
        CapabilityCatalogFactState.UNSUPPORTED
    )
