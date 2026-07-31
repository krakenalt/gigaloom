"""B5 end-to-end bindings across projects, routing, ACP, CLI, and Web."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zipfile import ZipFile

from gigaloom.cli_commands.handlers.capsules import (
    _handle_capsule_export,
    _handle_capsule_verify,
)
from gigaloom.config import HarnessConfig
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
from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpImplementationInfo,
    NegotiatedFeature,
)
from gigaloom.harnesses.api import (
    bind_structured_route_descriptors,
    build_capability_catalog,
    load_builtin_agent_profiles,
    project_acp_capability_snapshot,
)
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    ProjectCatalogService,
    ProjectLaunchProfileService,
)
from gigaloom.review.api import (
    FilesystemRunCapsuleRepository,
    RouteDecisionBindingsV1,
    RunCapsuleCapturePortsV1,
    RunCapsuleLifecycleService,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    create_route_decision_receipt,
    route_decision_receipt_to_dict,
)
from gigaloom.review.capsules import Ed25519Signer
from gigaloom.ui.services.run_capsules import RunCapsuleEvidenceQuery


FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"
NOW = "2026-07-31T12:00:00Z"
RUN_ID = "run_b5_e2e"


class _Inputs:
    def __init__(self, value):
        self.value = value

    def input_lock_for_run(self, run_id: str):
        assert run_id == RUN_ID
        return self.value


class _Outputs:
    def __init__(self, value):
        self.value = value

    def output_receipt_for_run(self, run_id: str):
        assert run_id == RUN_ID
        return self.value


class _Evidence:
    def __init__(self, artifacts, omissions):
        self.artifacts = artifacts
        self.omissions = omissions

    def artifact_manifest_for_run(self, run_id: str):
        assert run_id == RUN_ID
        return self.artifacts

    def omission_manifest_for_run(self, run_id: str):
        assert run_id == RUN_ID
        return self.omissions


class _ObservedInputs:
    def __init__(self, facts: dict[str, str | None]) -> None:
        self.facts = facts

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, str | None]:
        assert (run_id, owner_id, workspace_id) == (
            RUN_ID,
            "operator_b5",
            "workspace_b5",
        )
        return dict(self.facts)


def test_project_route_acp_and_cost_bindings_survive_capture_export_and_drift(
    tmp_path: Path,
    capsys,
) -> None:
    project_repository = FilesystemProjectCatalogRepository(tmp_path / "catalog")
    launch_repository = FilesystemLaunchProfileRepository(tmp_path / "launch")
    project_service = ProjectCatalogService(
        project_repository,
        clock=lambda: datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
    )
    launch_service = ProjectLaunchProfileService(
        launch_repository,
        project_repository,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = project_service.add_project(workspace, display_name="B5 fixture")
    launch_profile = launch_service.create_profile(
        project.catalog_project_id,
        display_name="Gemini read only",
        agent_hint="gemini",
        structured_route_hint="gemini.acp",
        workspace_policy_hint="read_only",
    )
    assert project.location.identity is not None

    profiles = load_builtin_agent_profiles()
    agent_profile = next(item for item in profiles if item.agent_id == "gemini")
    descriptors = bind_structured_route_descriptors(profiles)
    route = next(item for item in descriptors if item.route_id == "gemini.acp")
    acp_snapshot = AcpCapabilitySnapshotV1(
        protocol_version="1",
        client_info=AcpImplementationInfo("gigaloom", "GigaLoom", "0.7.0a1"),
        agent_info=AcpImplementationInfo("gemini", "Gemini CLI", "fixture"),
        agent_capabilities={},
        session_capabilities={},
        auth_capabilities={},
        negotiated_features=(NegotiatedFeature("structured_prompt"),),
        unsupported_features=(),
        compatibility_profile_digest=route.compatibility_profile_digest,
        process_fingerprint="1" * 64,
        connection_generation=1,
        snapshot_digest="2" * 64,
    )
    catalog = build_capability_catalog(
        descriptors,
        (project_acp_capability_snapshot("gemini.acp", acp_snapshot),),
    )
    satisfied = RouteFactState.SATISFIED
    operational_facts = tuple(
        RouteOperationalFactsV1(
            route_id=item.descriptor.route_id,
            workspace_policies=("read_only",),
            network_policies=("denied",),
            profile_admission=satisfied,
            executable_readiness=satisfied,
            version_readiness=satisfied,
            account_state=satisfied,
            account_digest="3" * 64,
            policy_state=satisfied,
            budget_state=satisfied,
            project_location_state=satisfied,
            sealed_evaluation_state=RouteFactState.UNKNOWN,
            session_portability_state=RouteFactState.UNKNOWN,
            cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
            compatibility_grade=CompatibilityGrade.READY,
            policy_priority=10,
        )
        for item in catalog.routes
    )
    context_digest = "4" * 64
    requirements = RouteRequirementsV1(
        intent=RouteIntent.READ,
        required_capabilities=("structured_prompt",),
        required_transport_classes=("acp_stdio_v1",),
        workspace_policy="read_only",
        network_policy="denied",
        cost_policy_ref="unknown_allowed_v1",
        platform="darwin",
        context_manifest_digest=context_digest,
        project_id=project.catalog_project_id,
        launch_profile_digest=launch_profile.digest,
        preferred_route_id="gemini.acp",
    )
    advice = advise_routes(
        requirements,
        candidates_from_capability_catalog(catalog, operational_facts),
    )
    assert advice.recommended_route_id == "gemini.acp"
    assert advice.eligible_routes[0].cost == RouteCostEvidence(
        RouteCostKnowledge.UNKNOWN
    )
    decision = create_route_decision_receipt(
        advice,
        RouteDecisionBindingsV1(
            task_digest="5" * 64,
            context_manifest_digest=context_digest,
            project_catalog_digest=project.digest,
            launch_profile_digest=launch_profile.digest,
            capability_catalog_digest=catalog.digest,
            cost_policy_digest="6" * 64,
        ),
        created_at=NOW,
    )
    decision_payload = route_decision_receipt_to_dict(decision)
    assert decision_payload["eligible_routes"][0]["cost"] == {
        "amount": None,
        "currency": None,
        "headroom": None,
        "knowledge": "unknown",
    }

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    input_payload = fixture["input_lock"]
    input_payload["project"] = {
        "catalog_id": project.catalog_project_id,
        "catalog_sha256": project.digest,
        "workspace_ref_sha256": project.location.identity,
    }
    input_payload["context_manifest"] = {
        "id": "context_b5",
        "sha256": context_digest,
    }
    input_payload["route_decision"] = {
        "id": decision.route_decision_id,
        "sha256": decision.receipt_digest,
    }
    input_payload["agent_profile"] = {
        "id": agent_profile.agent_id,
        "version": agent_profile.profile_version,
        "sha256": agent_profile.profile_digest,
    }
    input_payload["structured_route_id"] = "gemini.acp"
    input_payload["acp"] = {
        "protocol_version": acp_snapshot.protocol_version,
        "profile_sha256": acp_snapshot.compatibility_profile_digest,
        "capabilities_sha256": acp_snapshot.snapshot_digest,
    }
    input_payload["launch_profile_sha256"] = launch_profile.digest
    output_payload = fixture["output_receipt"]
    output_payload["run"] = {
        "run_id": RUN_ID,
        "attempt_id": "attempt_b5",
        "session_id": "session_b5",
    }
    output_payload["cost"] = {
        "knowledge": "unknown",
        "currency": None,
        "amount_micros": None,
        "receipt_sha256": None,
    }

    repository = FilesystemRunCapsuleRepository(tmp_path / "state")
    signer = Ed25519Signer.from_private_bytes(
        b"\x0b" * 32,
        signer_id="b5-test-signer",
        trust_status="test-only",
        key_rotation_id="b5-v1",
    )
    lifecycle = RunCapsuleLifecycleService(
        ports=RunCapsuleCapturePortsV1(
            _Inputs(build_input_lock(input_payload)),
            _Outputs(build_output_receipt(output_payload)),
            _Evidence(
                build_artifact_manifest(fixture["artifacts"]),
                build_omission_manifest(fixture["omissions"]),
            ),
        ),
        repository=repository,
        signer=signer,
    )
    completion_artifacts = lifecycle.on_run_completed(RUN_ID, completed_at=NOW)
    assert completion_artifacts[0].attributes["content_free"] is True

    exported = tmp_path / "b5-capsule.zip"
    config = HarnessConfig(data_dir=str(tmp_path / "state"))
    export_args = argparse.Namespace(
        run_id=RUN_ID,
        output=str(exported),
        json=True,
    )
    assert _handle_capsule_export(export_args, config) == 0
    export_report = json.loads(capsys.readouterr().out)
    assert export_report["signature_status"] == "signed"
    verify_args = argparse.Namespace(path=str(exported), checkout=None, json=True)
    assert _handle_capsule_verify(verify_args, config) == 0
    verify_report = json.loads(capsys.readouterr().out)
    assert verify_report["verified"] is True
    assert verify_report["correctness_claimed"] is False
    assert verify_report["network_accessed"] is False

    with ZipFile(exported) as archive:
        archive_root = export_report["capsule_id"]
        retained_input = json.loads(archive.read(f"{archive_root}/input-lock.json"))
        retained_output = json.loads(
            archive.read(f"{archive_root}/output-receipt.json")
        )
    assert retained_input["project"] == input_payload["project"]
    assert retained_input["route_decision"]["sha256"] == decision.receipt_digest
    assert retained_input["agent_profile"]["sha256"] == agent_profile.profile_digest
    assert retained_input["acp"] == input_payload["acp"]
    assert retained_input["launch_profile_sha256"] == launch_profile.digest
    assert retained_output["cost"] == output_payload["cost"]
    assert advice.eligible_routes[0].cost.amount is None

    observed_facts = {
        "project.catalog_sha256": project.digest,
        "project.workspace_ref_sha256": project.location.identity,
        "context_manifest.sha256": context_digest,
        "route_decision.sha256": decision.receipt_digest,
        "agent_profile.sha256": agent_profile.profile_digest,
        "structured_route_id": "gemini.acp",
        "acp.profile_sha256": acp_snapshot.compatibility_profile_digest,
        "acp.capabilities_sha256": acp_snapshot.snapshot_digest,
        "launch_profile_sha256": launch_profile.digest,
    }
    observed = _ObservedInputs(observed_facts)
    query = RunCapsuleEvidenceQuery(repository, observed)
    current = query.get(
        run_id=RUN_ID,
        owner_id="operator_b5",
        workspace_id="workspace_b5",
    )
    assert current.signature.valid is True
    assert current.drift.status == "current"
    assert current.drift.matched_count == len(observed_facts)

    observed.facts.update(
        {
            "project.catalog_sha256": "7" * 64,
            "structured_route_id": "codex.app-server",
            "acp.capabilities_sha256": "8" * 64,
        }
    )
    drifted = query.get(
        run_id=RUN_ID,
        owner_id="operator_b5",
        workspace_id="workspace_b5",
    )
    assert drifted.drift.status == "drifted"
    assert drifted.drift.drifted_count == 3
    assert {
        item.field for item in drifted.drift.findings if item.status == "drifted"
    } == {
        "acp.capabilities_sha256",
        "project.catalog_sha256",
        "structured_route_id",
    }
