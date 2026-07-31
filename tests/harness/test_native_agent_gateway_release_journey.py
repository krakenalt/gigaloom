"""F0-03 cross-feature Native Agent Gateway release journey."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.execution.api import (
    CompatibilityGrade,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteIntent,
    RouteOperationalFactsV1,
    RouteRequirementsV1,
    advise_routes,
    candidates_from_capability_catalog,
)
from gigaloom.harnesses.acp import (
    AcpLimits,
    AcpRouteIdentity,
    begin_prompt,
    create_acp_client,
    new_session,
    pin_acp_process,
    run_non_persisting_probe,
)
from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpImplementationInfo,
    NegotiatedFeature,
)
from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistry,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
)
from gigaloom.harnesses.api import (
    bind_structured_route_descriptors,
    build_capability_catalog,
    project_acp_capability_snapshot,
)
from gigaloom.native.api import TerminalContext
from gigaloom.native_cli_facade import run_native_namespace
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    LaunchResolutionContextV1,
    ProjectCatalogService,
    ProjectLaunchProfileService,
)
from gigaloom.review.api import (
    CurrentRouteRunEvidenceV1,
    FilesystemRunCapsuleRepository,
    RouteDecisionBindingsV1,
    RouteRunConfirmationV1,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    capture_run_capsule,
    create_route_decision_receipt,
    execute_confirmed_route,
    override_route_decision_receipt,
    verify_run_capsule,
)
from gigaloom.review.capsules import Ed25519Signer
from gigaloom.tools.mcp.apps import (
    MCP_APP_HTML_MIME_TYPE,
    MCPAppFallbackCode,
    MCPAppResourceCandidate,
    MCPAppServerIdentity,
    admit_mcp_app_resource,
)
from gigaloom.ui.routers.run_capsules import create_router
from gigaloom.ui.services.run_capsules import RunCapsuleEvidenceQuery


ROOT = Path(__file__).parents[2]
ACP_FIXTURE = ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"
CAPSULE_FIXTURE = ROOT / "tests" / "fixtures" / "run_capsules" / "read_only_run.json"
RUN_ID = "run_native_agent_gateway_f0"
CONTEXT_DIGEST = "4" * 64
TASK_DIGEST = "5" * 64
NOW = "2026-07-31T18:03:00Z"


class _CurrentEvidence:
    def __init__(self, evidence: CurrentRouteRunEvidenceV1) -> None:
        self.evidence = evidence
        self.requests: list[tuple[str, str]] = []

    def current_evidence(self, *, route_decision_id: str, route_id: str):
        self.requests.append((route_decision_id, route_id))
        return self.evidence


class _ReadOnlyAcpRunner:
    def __init__(self, *, workspace: Path, compatibility_digest: str) -> None:
        self.workspace = workspace
        self.compatibility_digest = compatibility_digest
        self.calls: list[str] = []

    def run(self, plan, request):
        assert request == {
            "intent": "read",
            "workspace_policy": "read_only",
        }
        assert plan.fallback_allowed is False
        assert plan.execution_attempts == 1
        native_home = self.workspace.parent / "acp-home"
        native_home.mkdir()
        spec = pin_acp_process(
            (ACP_FIXTURE.resolve().as_posix(),),
            cwd=self.workspace,
            environment={
                "HOME": native_home.as_posix(),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            },
            allowed_environment=frozenset({"HOME"}),
        )
        client = create_acp_client(
            spec,
            compatibility_profile_digest=self.compatibility_digest,
            route_identity=AcpRouteIdentity(
                plan.agent_id,
                plan.route_id,
                self.compatibility_digest,
            ),
            limits=AcpLimits(request_timeout_seconds=3.0),
        )
        try:
            client.start()
            snapshot = client.initialize()
            session = new_session(client, workspace=self.workspace)
            result = begin_prompt(client, session, text="read only").result(1.0)
        finally:
            client.close()
        self.calls.append(plan.route_id)
        return {
            "route_id": plan.route_id,
            "protocol_version": snapshot.protocol_version,
            "stop_reason": result.stop_reason,
            "total_tokens": result.usage.total_tokens if result.usage else None,
        }


class _ObservedInputs:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, str | None]:
        assert (run_id, owner_id, workspace_id) == (
            RUN_ID,
            "local_operator",
            "workspace_f0",
        )
        return dict(self.values)


def test_native_agent_gateway_release_journey(tmp_path: Path) -> None:
    profiles = load_builtin_agent_profiles()
    registry = AgentProfileRegistry.build(
        profiles,
        collision_contract=build_core_command_collision_contract(
            ("agent", "project", "route", "run", "ui")
        ),
    )
    assert {profile.agent_id for profile in registry.profiles} >= {
        "codex",
        "claude",
        "gemini",
        "pi",
    }

    workspace = tmp_path / "repository"
    workspace.mkdir()
    project_state = tmp_path / "state" / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(project_state / "catalog")
    project = ProjectCatalogService(
        catalog_repository,
        clock=lambda: datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc),
    ).add_project(workspace, display_name="Native Agent Gateway journey")
    profile_service = ProjectLaunchProfileService(
        FilesystemLaunchProfileRepository(project_state / "launch_profiles"),
        catalog_repository,
    )
    launch_profile = profile_service.create_profile(
        project.catalog_project_id,
        display_name="Pi read only",
        agent_hint="pi",
        structured_route_hint="pi.acp",
        workspace_policy_hint="read_only",
        terminal_mode_hint="direct",
    )

    descriptors = tuple(
        descriptor
        for descriptor in bind_structured_route_descriptors(profiles)
        if descriptor.route_id in {"gemini.acp", "pi.acp"}
    )
    assert {descriptor.route_id for descriptor in descriptors} == {
        "gemini.acp",
        "pi.acp",
    }
    resolved_launch = profile_service.resolve_profile(
        launch_profile.launch_profile_id,
        LaunchResolutionContextV1(
            agent_ids=frozenset(profile.agent_id for profile in registry.profiles),
            structured_route_ids=frozenset(
                descriptor.route_id for descriptor in descriptors
            ),
            workspace_policies=frozenset({"read_only"}),
            terminal_modes=frozenset({"direct"}),
        ),
    )
    assert (resolved_launch.agent_id, resolved_launch.structured_route_id) == (
        "pi",
        "pi.acp",
    )
    assert resolved_launch.unsatisfied_hints == ()
    assert resolved_launch.authority_granted is False

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_pi = bin_dir / "pi"
    fake_pi.write_text(
        '#!/bin/sh\n[ "$1" = "--version" ] || exit 7\nprintf \'pi fixture 1.0\\n\'\n',
        encoding="utf-8",
    )
    fake_pi.chmod(0o755)
    native_calls: list[tuple[str, tuple[str, ...], str]] = []

    def native_runner(process_spec, suffix, **kwargs):
        environment = dict(kwargs["environment"])
        executable = shutil.which(process_spec.executable, path=environment["PATH"])
        assert executable is not None
        completed = subprocess.run(
            (executable, *suffix),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        native_calls.append((process_spec.namespace, tuple(suffix), completed.stdout))
        return completed.returncode

    assert (
        run_native_namespace(
            ("pi", "--version"),
            registry=registry,
            environment={"PATH": str(bin_dir)},
            runner=native_runner,
            managed_runner=native_runner,
            context=TerminalContext(False, False, False, "", platform="darwin"),
            managed_terminal_supported=False,
        )
        == 0
    )
    assert native_calls == [("pi", ("--version",), "pi fixture 1.0\n")]

    pi_descriptor = next(item for item in descriptors if item.route_id == "pi.acp")
    probe = run_non_persisting_probe(
        (ACP_FIXTURE.resolve().as_posix(),),
        route_identity=AcpRouteIdentity(
            "pi",
            "pi.acp",
            pi_descriptor.compatibility_profile_digest,
        ),
        network_isolated=True,
    )
    assert probe.state == "ready"
    assert probe.session_created is False
    assert probe.prompt_sent is False

    snapshots = tuple(
        project_acp_capability_snapshot(
            descriptor.route_id,
            _capability_snapshot(
                descriptor,
                probe=probe if descriptor.route_id == "pi.acp" else None,
            ),
        )
        for descriptor in descriptors
    )
    catalog = build_capability_catalog(descriptors, snapshots)
    satisfied = RouteFactState.SATISFIED
    candidates = candidates_from_capability_catalog(
        catalog,
        tuple(
            RouteOperationalFactsV1(
                route_id=route.descriptor.route_id,
                workspace_policies=("read_only",),
                network_policies=("denied",),
                profile_admission=satisfied,
                executable_readiness=satisfied,
                version_readiness=satisfied,
                account_state=satisfied,
                account_digest=(
                    "a" if route.descriptor.route_id == "gemini.acp" else "b"
                )
                * 64,
                policy_state=satisfied,
                budget_state=satisfied,
                project_location_state=satisfied,
                sealed_evaluation_state=RouteFactState.UNKNOWN,
                session_portability_state=RouteFactState.UNKNOWN,
                cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
                compatibility_grade=CompatibilityGrade.READY,
                policy_priority=(
                    10 if route.descriptor.route_id == "gemini.acp" else 20
                ),
            )
            for route in catalog.routes
        ),
    )
    advice = advise_routes(
        RouteRequirementsV1(
            intent=RouteIntent.READ,
            required_capabilities=("structured_prompt",),
            required_transport_classes=("acp_stdio_v1",),
            workspace_policy="read_only",
            network_policy="denied",
            cost_policy_ref="unknown_allowed_v1",
            platform="darwin",
            context_manifest_digest=CONTEXT_DIGEST,
            project_id=project.catalog_project_id,
            launch_profile_digest=launch_profile.digest,
        ),
        candidates,
    )
    assert len(advice.eligible_routes) == 2
    assert advice.recommended_route_id == "gemini.acp"
    decision = create_route_decision_receipt(
        advice,
        RouteDecisionBindingsV1(
            task_digest=TASK_DIGEST,
            context_manifest_digest=CONTEXT_DIGEST,
            project_catalog_digest=project.digest,
            launch_profile_digest=launch_profile.digest,
            capability_catalog_digest=catalog.digest,
            cost_policy_digest="6" * 64,
        ),
        created_at="2026-07-31T18:00:00Z",
    )
    selected_decision = override_route_decision_receipt(
        decision,
        route_id="pi.acp",
        reason_code="operator_selected",
        created_at="2026-07-31T18:01:00Z",
    )
    assert selected_decision.override is not None
    assert selected_decision.recommended_route_id == "pi.acp"

    selected = next(
        item for item in selected_decision.eligible_routes if item.route_id == "pi.acp"
    )
    current = _CurrentEvidence(
        CurrentRouteRunEvidenceV1(
            bindings=selected_decision.bindings,
            route_id=selected.route_id,
            agent_id=selected.agent_id,
            profile_digest=selected.profile_digest,
            capability_snapshot_digest=selected.capability_snapshot_digest,
            account_digest=selected.account_digest,
            transport_class=selected.transport_class,
            cost=selected.cost,
            eligible=True,
        )
    )
    structured_runner = _ReadOnlyAcpRunner(
        workspace=workspace,
        compatibility_digest=pi_descriptor.compatibility_profile_digest,
    )
    run_result = execute_confirmed_route(
        selected_decision,
        confirmation=RouteRunConfirmationV1(
            confirmation_id="confirm_native_agent_gateway_f0",
            operator_id="operator_f0",
            route_decision_id=selected_decision.route_decision_id,
            receipt_digest=selected_decision.receipt_digest,
            route_id="pi.acp",
            confirmed_at="2026-07-31T18:02:00Z",
        ),
        request={"intent": "read", "workspace_policy": "read_only"},
        evidence_source=current,
        runner=structured_runner,
    )
    assert run_result == {
        "route_id": "pi.acp",
        "protocol_version": "1",
        "stop_reason": "end_turn",
        "total_tokens": 3,
    }
    assert structured_runner.calls == ["pi.acp"]
    assert current.requests == [(selected_decision.route_decision_id, "pi.acp")]

    fallback = admit_mcp_app_resource(
        MCPAppResourceCandidate(
            server=MCPAppServerIdentity("local-evidence", True, True, True),
            uri="ui://local-evidence/run-summary",
            mime_type=MCP_APP_HTML_MIME_TYPE,
            html=b"<p>read-only run evidence</p>",
            expected_sha256="0" * 64,
            textual_fallback="Read-only run evidence is available as JSON.",
            structured_fallback={"run_id": RUN_ID, "status": "succeeded"},
        )
    )
    assert fallback.resource is None
    assert fallback.fallback is not None
    assert fallback.fallback.code is MCPAppFallbackCode.DIGEST_MISMATCH
    assert fallback.fallback.structured["run_id"] == RUN_ID

    capsule_payload = json.loads(CAPSULE_FIXTURE.read_text(encoding="utf-8"))
    input_payload = capsule_payload["input_lock"]
    input_payload["project"] = {
        "catalog_id": project.catalog_project_id,
        "catalog_sha256": project.digest,
        "workspace_ref_sha256": project.location.identity,
    }
    input_payload["context_manifest"] = {
        "id": "context_native_agent_gateway_f0",
        "sha256": CONTEXT_DIGEST,
    }
    input_payload["route_decision"] = {
        "id": selected_decision.route_decision_id,
        "sha256": selected_decision.receipt_digest,
    }
    input_payload["agent_profile"] = {
        "id": "pi",
        "version": registry.get("pi").profile_version,
        "sha256": registry.get("pi").profile_digest,
    }
    input_payload["structured_route_id"] = "pi.acp"
    input_payload["acp"] = {
        "protocol_version": probe.protocol_version,
        "profile_sha256": pi_descriptor.compatibility_profile_digest,
        "capabilities_sha256": probe.capability_snapshot_digest,
    }
    input_payload["launch_profile_sha256"] = launch_profile.digest
    input_payload["executable"] = {
        "path_sha256": _digest(fake_pi.resolve().as_posix()),
        "sha256": sha256(fake_pi.read_bytes()).hexdigest(),
        "version": "pi-fixture-1.0",
    }
    output_payload = capsule_payload["output_receipt"]
    output_payload["run"] = {
        "run_id": RUN_ID,
        "attempt_id": "attempt_native_agent_gateway_f0",
        "session_id": "session_native_agent_gateway_f0",
    }
    output_payload["process"]["receipt_sha256"] = _digest(
        json.dumps(run_result, sort_keys=True)
    )
    bundle = capture_run_capsule(
        input_lock=build_input_lock(input_payload),
        output_receipt=build_output_receipt(output_payload),
        artifacts=build_artifact_manifest(capsule_payload["artifacts"]),
        omissions=build_omission_manifest(capsule_payload["omissions"]),
        created_at=NOW,
        signer=Ed25519Signer.from_private_bytes(
            b"\x0f" * 32,
            signer_id="native-agent-gateway-f0",
            trust_status="test-only",
            key_rotation_id="f0-v1",
        ),
    )
    capsule_repository = FilesystemRunCapsuleRepository(tmp_path / "state")
    capsule_repository.save(RUN_ID, bundle, created_at=NOW)

    observed = {
        "project.catalog_sha256": project.digest,
        "route_decision.sha256": selected_decision.receipt_digest,
        "agent_profile.sha256": registry.get("pi").profile_digest,
        "structured_route_id": "pi.acp",
        "acp.capabilities_sha256": probe.capability_snapshot_digest,
        "launch_profile_sha256": launch_profile.digest,
    }
    app = FastAPI()
    app.include_router(
        create_router(
            RunCapsuleEvidenceQuery(
                capsule_repository,
                _ObservedInputs(observed),
            )
        )
    )
    response = TestClient(app).get(
        f"/api/operator/runs/{RUN_ID}/capsule",
        params={"workspace_id": "workspace_f0"},
    )
    assert response.status_code == 200
    web_evidence = response.json()["capsule"]
    assert web_evidence["integrity_status"] == "verified"
    assert web_evidence["content_free"] is True
    assert web_evidence["correctness_claimed"] is False
    assert web_evidence["signature"]["valid"] is True
    assert web_evidence["drift"]["status"] == "current"
    assert web_evidence["drift"]["matched_count"] == len(observed)

    exported = capsule_repository.export_run(
        RUN_ID,
        tmp_path / "native-agent-gateway-capsule.zip",
    )
    verification = verify_run_capsule(exported, observed_inputs=observed)
    assert verification.verified is True
    assert verification.signature_valid is True
    assert verification.capsule_id == web_evidence["capsule_id"]


def _capability_snapshot(descriptor, *, probe) -> AcpCapabilitySnapshotV1:
    if probe is None:
        process_fingerprint = "c" * 64
        snapshot_digest = "d" * 64
    else:
        process_fingerprint = probe.process_fingerprint
        snapshot_digest = probe.capability_snapshot_digest
    return AcpCapabilitySnapshotV1(
        protocol_version="1",
        client_info=AcpImplementationInfo("gigaloom", "GigaLoom", "0.7.0a1"),
        agent_info=AcpImplementationInfo(
            descriptor.agent_id,
            f"{descriptor.agent_id} fixture",
            "1.0.0",
        ),
        agent_capabilities={},
        session_capabilities={},
        auth_capabilities={},
        negotiated_features=(NegotiatedFeature("structured_prompt"),),
        unsupported_features=(),
        compatibility_profile_digest=descriptor.compatibility_profile_digest,
        process_fingerprint=process_fingerprint,
        connection_generation=1,
        snapshot_digest=snapshot_digest,
    )


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()
