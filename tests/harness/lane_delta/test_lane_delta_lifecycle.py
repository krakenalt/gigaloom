"""Verified lane-delta integration with session and Run Capsule lifecycles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigaloom.config import HarnessConfig
from gigaloom.harnesses.base import BaseHarness
from gigaloom.provider_authentication_broker import ProviderSessionBinding
from gigaloom.registry import HarnessRegistry
from gigaloom.review.api import (
    CapsuleError,
    FilesystemLaneDeltaPacketStore,
    FilesystemRunCapsuleRepository,
    LANE_DELTA_METADATA_KEY,
    LANE_STATE_METADATA_KEY,
    LaneDeltaBuilder,
    LaneDeltaLifecycleService,
    RunCapsuleCapturePortsV1,
    RunCapsuleLifecycleService,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
)
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.types import (
    Availability,
    HarnessCapability,
    HarnessContext,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
)
from gigaloom.ui.services.run_capsules import (
    RunCapsuleEvidenceQuery,
    SessionLaneDeltaReferenceProvider,
)


FIXTURE = Path(__file__).parents[2] / "fixtures" / "run_capsules" / "read_only_run.json"


class _RouteA(BaseHarness):
    @classmethod
    def spec(cls) -> HarnessSpec:
        return _spec("route-a")

    def availability(self) -> Availability:
        return Availability.available("test")

    def run(self, request: HarnessRequest, context: HarnessContext) -> HarnessResult:
        del context
        return HarnessResult(ok=True, text=f"answer:{request.prompt}")


class _RouteB(_RouteA):
    @classmethod
    def spec(cls) -> HarnessSpec:
        return _spec("route-b")


class _ProviderRoute(_RouteA):
    @classmethod
    def spec(cls) -> HarnessSpec:
        return _spec("codex-cli")


class _CapsulePorts:
    def __init__(self) -> None:
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def input_lock_for_run(self, run_id: str):
        del run_id
        return build_input_lock(self.payload["input_lock"])

    def output_receipt_for_run(self, run_id: str):
        payload = dict(self.payload["output_receipt"])
        payload["run"] = {**payload["run"], "run_id": run_id}
        return build_output_receipt(payload)

    def artifact_manifest_for_run(self, run_id: str):
        del run_id
        return build_artifact_manifest(self.payload["artifacts"])

    def omission_manifest_for_run(self, run_id: str):
        del run_id
        return build_omission_manifest(self.payload["omissions"])


class _AccountProvider:
    def __init__(self, account_identity: str) -> None:
        self.account_identity = account_identity

    def session_binding(self, provider_id: str) -> ProviderSessionBinding:
        return ProviderSessionBinding(
            provider_id=provider_id,
            account_identity=self.account_identity,
            home_identity="home-fixture",
            source_identity="source-fixture",
            identity_evidence="test",
            authentication_method="test",
            observed_at="2026-08-01T12:00:00Z",
        )


class _ObservedInputs:
    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, str | None]:
        del run_id, owner_id, workspace_id
        return {}


def test_route_switch_emits_verified_packet_and_unchanged_lane_does_not(
    tmp_path: Path,
) -> None:
    runner, packet_store, capsule_repository = _runner(tmp_path)
    session = runner.create_session(
        default_harness_id="route-a",
        default_model="model-a",
    )

    first = runner.run_in_session(
        session.id,
        {"harness_id": "route-a", "model": "model-a", "prompt": "secret-one"},
    )
    switched = runner.run_in_session(
        session.id,
        {"harness_id": "route-b", "model": "model-a", "prompt": "secret-two"},
    )
    unchanged = runner.run_in_session(
        session.id,
        {"harness_id": "route-b", "model": "model-a", "prompt": "secret-three"},
    )

    assert LANE_DELTA_METADATA_KEY not in first.run.metadata
    reference = switched.run.metadata[LANE_DELTA_METADATA_KEY]
    assert reference["changed_selectors"] == ["route_id"]
    assert reference["content_free"] is True
    assert reference["hidden_state_portability_claimed"] is False
    assert [item["status"] for item in reference["run_capsule_references"]] == [
        "captured",
        "captured",
    ]
    stored = packet_store.load_by_digests(
        reference["packet_id"],
        expected_source_lane_digest=reference["source_lane_sha256"],
        expected_destination_lane_digest=reference["destination_lane_sha256"],
    )
    assert stored.packet_sha256 == reference["packet_sha256"]
    assert stored.packet.missed_turn_range.first_sequence == 2
    assert stored.packet.missed_turn_range.last_sequence == 2
    assert "route_id_changed" in {
        item.reason_code for item in stored.packet.changed_anchors
    }
    assert capsule_repository.get_by_run(first.run.id).capsule_sha256 in (
        stored.packet.run_capsule_digests
    )
    assert capsule_repository.get_by_run(switched.run.id).capsule_sha256 in (
        stored.packet.run_capsule_digests
    )
    assert LANE_DELTA_METADATA_KEY not in unchanged.run.metadata
    assert [item["kind"] for item in switched.run.metadata["completion_artifacts"]] == [
        "lane_delta",
        "run_capsule",
    ]
    assert [
        item["kind"] for item in unchanged.run.metadata["completion_artifacts"]
    ] == ["run_capsule"]
    retained = next(packet_store.root.glob("*.json")).read_bytes()
    assert b"secret-one" not in retained
    assert b"secret-two" not in retained

    evidence = RunCapsuleEvidenceQuery(
        capsule_repository,
        _ObservedInputs(),
        SessionLaneDeltaReferenceProvider(runner.store, packet_store),
    ).get(
        run_id=switched.run.id,
        owner_id="operator",
        workspace_id="workspace",
    )
    assert len(evidence.references) == 1
    assert evidence.references[0].packet_sha256 == reference["packet_sha256"]
    assert evidence.references[0].run_capsule_references[1].sha256 == (
        evidence.capsule_sha256
    )


@pytest.mark.parametrize(
    ("first_fields", "second_fields", "selector", "reason_code"),
    [
        (
            {"agent_id": "agent-a"},
            {"agent_id": "agent-b"},
            "agent_id",
            "agent_id_changed",
        ),
        (
            {"model": "model-a"},
            {"model": "model-b"},
            "model_id",
            "model_id_changed",
        ),
        (
            {"native_session_id": "native-a"},
            {"native_session_id": "native-b"},
            "session_id",
            "session_id_changed",
        ),
    ],
)
def test_each_explicit_selector_change_emits_one_packet(
    tmp_path: Path,
    first_fields: dict[str, str],
    second_fields: dict[str, str],
    selector: str,
    reason_code: str,
) -> None:
    runner, packet_store, _ = _runner(tmp_path)
    session = runner.create_session(default_harness_id="route-a")
    common = {"harness_id": "route-a", "prompt": "turn"}

    runner.run_in_session(session.id, {**common, **first_fields})
    switched = runner.run_in_session(session.id, {**common, **second_fields})

    reference = switched.run.metadata[LANE_DELTA_METADATA_KEY]
    assert reference["changed_selectors"] == [selector]
    stored = packet_store.load_by_digests(
        reference["packet_id"],
        expected_source_lane_digest=reference["source_lane_sha256"],
        expected_destination_lane_digest=reference["destination_lane_sha256"],
    )
    assert reason_code in {item.reason_code for item in stored.packet.changed_anchors}


def test_explicit_fork_and_account_change_emit_session_and_account_anchors(
    tmp_path: Path,
) -> None:
    account_provider = _AccountProvider("account-a")
    runner, packet_store, _ = _runner(
        tmp_path,
        harnesses=(_ProviderRoute(),),
        account_provider=account_provider,
    )
    source = runner.create_session(default_harness_id="codex-cli")
    first = runner.run_in_session(
        source.id,
        {"harness_id": "codex-cli", "prompt": "first"},
    )
    account_provider.account_identity = "account-b"
    fork = runner.create_session(
        default_harness_id="codex-cli",
        metadata={
            "forked_from_run_id": first.run.id,
            LANE_STATE_METADATA_KEY: first.run.metadata[LANE_STATE_METADATA_KEY],
        },
    )

    switched = runner.run_in_session(
        fork.id,
        {"harness_id": "codex-cli", "prompt": "second"},
    )

    reference = switched.run.metadata[LANE_DELTA_METADATA_KEY]
    assert reference["changed_selectors"] == ["account_ref", "session_id"]
    stored = packet_store.load_by_digests(
        reference["packet_id"],
        expected_source_lane_digest=reference["source_lane_sha256"],
        expected_destination_lane_digest=reference["destination_lane_sha256"],
    )
    assert {item.reason_code for item in stored.packet.changed_anchors} >= {
        "account_ref_changed",
        "session_id_changed",
    }


def test_capsule_reference_fails_closed_when_run_metadata_changes(
    tmp_path: Path,
) -> None:
    runner, packet_store, capsule_repository = _runner(tmp_path)
    session = runner.create_session(default_harness_id="route-a")
    runner.run_in_session(
        session.id,
        {"harness_id": "route-a", "prompt": "first"},
    )
    switched = runner.run_in_session(
        session.id,
        {"harness_id": "route-b", "prompt": "second"},
    )
    metadata = dict(switched.run.metadata)
    reference = dict(metadata[LANE_DELTA_METADATA_KEY])
    reference["packet_sha256"] = "0" * 64
    metadata[LANE_DELTA_METADATA_KEY] = reference
    runner.store.update_run(switched.run.id, metadata=metadata)
    query = RunCapsuleEvidenceQuery(
        capsule_repository,
        _ObservedInputs(),
        SessionLaneDeltaReferenceProvider(runner.store, packet_store),
    )

    with pytest.raises(CapsuleError, match="changed after capture"):
        query.get(
            run_id=switched.run.id,
            owner_id="operator",
            workspace_id="workspace",
        )


def test_missing_capsule_capture_is_declared_without_content_or_portability_claim(
    tmp_path: Path,
) -> None:
    runner, packet_store, _ = _runner(tmp_path, capture_capsules=False)
    session = runner.create_session(default_harness_id="route-a")
    runner.run_in_session(
        session.id,
        {"harness_id": "route-a", "prompt": "first-private-turn"},
    )
    switched = runner.run_in_session(
        session.id,
        {"harness_id": "route-b", "prompt": "second-private-turn"},
    )

    reference = switched.run.metadata[LANE_DELTA_METADATA_KEY]
    assert [item["status"] for item in reference["run_capsule_references"]] == [
        "not_captured",
        "not_captured",
    ]
    assert reference["hidden_state_portability_claimed"] is False
    assert {
        "source_run_capsule_not_captured",
        "destination_run_capsule_not_captured",
    } <= set(reference["omissions"])
    packet_bytes = next(packet_store.root.glob("*.json")).read_bytes()
    assert b"private-turn" not in packet_bytes


def _runner(
    tmp_path: Path,
    *,
    harnesses: tuple[BaseHarness, ...] = (_RouteA(), _RouteB()),
    account_provider: _AccountProvider | None = None,
    capture_capsules: bool = True,
) -> tuple[
    HarnessSessionRunner,
    FilesystemLaneDeltaPacketStore,
    FilesystemRunCapsuleRepository,
]:
    registry = HarnessRegistry()
    for harness in harnesses:
        registry.register(harness)
    session_store = InMemoryHarnessSessionStore()
    packet_store = FilesystemLaneDeltaPacketStore(tmp_path / "state")
    capsule_repository = FilesystemRunCapsuleRepository(tmp_path / "state")
    ports = _CapsulePorts()
    capsule_lifecycle = (
        RunCapsuleLifecycleService(
            ports=RunCapsuleCapturePortsV1(ports, ports, ports),
            repository=capsule_repository,
        )
        if capture_capsules
        else None
    )
    lane_lifecycle = LaneDeltaLifecycleService(
        session_store=session_store,
        builder=LaneDeltaBuilder(),
        packet_store=packet_store,
        capsule_repository=capsule_repository,
        delegate=capsule_lifecycle,
    )
    runner = HarnessSessionRunner(
        registry=registry,
        config=HarnessConfig(data_dir=str(tmp_path / "state")),
        store=session_store,
        provider_account_provider=account_provider,
        run_completion_hook=lane_lifecycle,
        run_lane_lifecycle=lane_lifecycle,
    )
    return runner, packet_store, capsule_repository


def _spec(harness_id: str) -> HarnessSpec:
    return HarnessSpec(
        id=harness_id,
        title=harness_id,
        kind="test",
        description="lane lifecycle fixture",
        capabilities=(HarnessCapability.CHAT_COMPLETIONS,),
    )
