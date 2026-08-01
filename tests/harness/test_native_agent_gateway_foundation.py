"""Composition checks for the native-agent gateway foundations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from gigaloom.execution.api import (
    CompatibilityGrade,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteOperationalFactsV1,
    candidates_from_capability_catalog,
)
from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpImplementationInfo,
    CapabilityLoss,
    NegotiatedFeature,
)
from gigaloom.harnesses.api import (
    CapabilityCatalogFactState,
    CapabilitySnapshotState,
    bind_structured_route_descriptors,
    build_capability_catalog,
    load_builtin_agent_profiles,
    project_acp_capability_snapshot,
)
from gigaloom.projects.api import (
    NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1,
    PROJECT_CATALOG_MIGRATION_ID,
    TEXTUAL_PREFERENCES_RETIREMENT_ID,
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    ProjectCatalogService,
    ProjectLaunchProfileService,
    ProjectLaunchReadService,
    validate_migration_sequence,
)
from gigaloom.review.api import (
    RunCapsuleCapturePortsV1,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    capture_run_capsule_from_ports,
)


CAPSULE_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"
)


def test_profiles_bind_to_catalog_without_promoting_metadata_to_capability() -> None:
    profiles = load_builtin_agent_profiles()
    descriptors = bind_structured_route_descriptors(profiles)

    assert tuple(item.route_id for item in descriptors) == (
        "claude.stream-json",
        "codex.app-server",
        "gemini.acp",
        "pi.acp",
    )
    catalog = build_capability_catalog(descriptors)
    assert catalog == build_capability_catalog(descriptors)
    assert all(
        route.snapshot_state is CapabilitySnapshotState.UNKNOWN
        for route in catalog.routes
    )
    assert all(
        fact.state is CapabilityCatalogFactState.UNKNOWN
        for route in catalog.routes
        for fact in route.capabilities
    )


def test_acp_snapshot_and_operational_facts_compose_route_candidates() -> None:
    descriptors = bind_structured_route_descriptors(load_builtin_agent_profiles())
    gemini = next(item for item in descriptors if item.route_id == "gemini.acp")
    snapshot = AcpCapabilitySnapshotV1(
        protocol_version="1",
        client_info=AcpImplementationInfo("gigaloom", "GigaLoom", "0.7.0a1"),
        agent_info=AcpImplementationInfo("fixture-agent", None, "1.0.0"),
        agent_capabilities={},
        session_capabilities={},
        auth_capabilities={},
        negotiated_features=(NegotiatedFeature("structured_prompt"),),
        unsupported_features=(
            CapabilityLoss("session_load", "unsupported", "not_advertised"),
        ),
        compatibility_profile_digest=gemini.compatibility_profile_digest,
        process_fingerprint="a" * 64,
        connection_generation=1,
        snapshot_digest="b" * 64,
    )
    catalog = build_capability_catalog(
        descriptors,
        (project_acp_capability_snapshot("gemini.acp", snapshot),),
    )
    satisfied = RouteFactState.SATISFIED
    facts = tuple(
        RouteOperationalFactsV1(
            route_id=route.descriptor.route_id,
            workspace_policies=("read_only",),
            network_policies=("denied",),
            profile_admission=satisfied,
            executable_readiness=satisfied,
            version_readiness=satisfied,
            account_state=RouteFactState.UNKNOWN,
            account_digest=None,
            policy_state=satisfied,
            budget_state=satisfied,
            project_location_state=satisfied,
            sealed_evaluation_state=RouteFactState.UNKNOWN,
            session_portability_state=RouteFactState.UNKNOWN,
            cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
            compatibility_grade=CompatibilityGrade.READY,
            policy_priority=0,
        )
        for route in catalog.routes
    )

    candidates = candidates_from_capability_catalog(catalog, facts)
    projected = {item.route_id: item for item in candidates}
    assert projected["gemini.acp"].capability_snapshot_state is satisfied
    assert projected["gemini.acp"].capabilities[0].state is RouteFactState.REJECTED
    assert projected["gemini.acp"].capabilities[1].state is satisfied
    assert (
        projected["codex.app-server"].capability_snapshot_state
        is RouteFactState.UNKNOWN
    )
    assert projected["codex.app-server"].cost.amount is None


def test_project_launch_read_model_and_wave_a_migration_registry_are_bounded(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(data_dir / "catalog")
    profile_repository = FilesystemLaunchProfileRepository(data_dir / "launch_profiles")
    catalog_service = ProjectCatalogService(
        catalog_repository,
        clock=lambda: datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
    )
    profile_service = ProjectLaunchProfileService(
        profile_repository,
        catalog_repository,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = catalog_service.add_project(workspace, display_name="Fixture")
    profile = profile_service.create_profile(
        project.catalog_project_id,
        display_name="Read only",
        agent_hint="gemini",
        structured_route_hint="gemini.acp",
    )
    reader = ProjectLaunchReadService(catalog_repository, profile_repository)

    snapshot = reader.read(project.catalog_project_id, limit=1)
    assert snapshot.project == project
    assert snapshot.launch_profiles == (profile,)
    assert snapshot == reader.read(project.catalog_project_id, limit=1)
    sequence = validate_migration_sequence(NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1)
    assert tuple(item.migration_id for item in sequence) == (
        PROJECT_CATALOG_MIGRATION_ID,
        TEXTUAL_PREFERENCES_RETIREMENT_ID,
    )
    assert sequence[0].requires_backup is True
    assert sequence[0].automatic is False


def test_capsule_capture_ports_compose_without_live_execution_import() -> None:
    payload = json.loads(CAPSULE_FIXTURE.read_text(encoding="utf-8"))

    class Inputs:
        calls = 0

        def input_lock_for_run(self, run_id: str):
            self.calls += 1
            assert run_id == "run_fixture"
            return build_input_lock(payload["input_lock"])

    class Outputs:
        calls = 0

        def output_receipt_for_run(self, run_id: str):
            self.calls += 1
            assert run_id == "run_fixture"
            return build_output_receipt(payload["output_receipt"])

    class Evidence:
        artifact_calls = 0
        omission_calls = 0

        def artifact_manifest_for_run(self, run_id: str):
            self.artifact_calls += 1
            assert run_id == "run_fixture"
            return build_artifact_manifest(payload["artifacts"])

        def omission_manifest_for_run(self, run_id: str):
            self.omission_calls += 1
            assert run_id == "run_fixture"
            return build_omission_manifest(payload["omissions"])

    inputs = Inputs()
    outputs = Outputs()
    evidence = Evidence()
    bundle = capture_run_capsule_from_ports(
        "run_fixture",
        RunCapsuleCapturePortsV1(inputs, outputs, evidence),
        created_at=payload["created_at"],
    )

    assert bundle.capsule.capsule_id.startswith("capsule_")
    assert inputs.calls == outputs.calls == 1
    assert evidence.artifact_calls == evidence.omission_calls == 1
