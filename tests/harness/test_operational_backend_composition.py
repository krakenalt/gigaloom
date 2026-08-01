"""Public-facade and application ownership checks for operational backends."""

from gigaloom.automation import api as automation_api
from gigaloom.automation.evaluations.visual import api as visual_api
from gigaloom.config import HarnessConfig
from gigaloom.diagnostics import api as diagnostics_api
from gigaloom.diagnostics import fault_lab, recovery, upgrade_radar
from gigaloom.registry import create_default_registry
from gigaloom.review import api as review_api
from gigaloom.review.handoffs.lane_delta import api as lane_delta_api
from gigaloom.runtime import api as runtime_api
from gigaloom.runtime import credentials
from gigaloom.ui.app import create_app
from gigaloom.ui.dependencies import app_services


def test_bounded_context_facades_export_operational_backends() -> None:
    """Cross-context callers receive the exact owning implementations."""
    assert runtime_api.InMemoryCredentialBroker is credentials.InMemoryCredentialBroker
    assert review_api.LaneDeltaBuilder is lane_delta_api.LaneDeltaBuilder
    assert (
        review_api.planned_changed_selectors is lane_delta_api.planned_changed_selectors
    )
    assert automation_api.run_visual_gate is visual_api.run_visual_gate
    assert diagnostics_api.RecoveryReceiptService is recovery.RecoveryReceiptService
    assert diagnostics_api.FaultLabRunner is fault_lab.FaultLabRunner
    assert (
        diagnostics_api.compare_route_evaluations
        is upgrade_radar.compare_route_evaluations
    )


def test_application_container_owns_stateful_operational_backends(tmp_path) -> None:
    """One application graph owns safe defaults without eagerly writing state."""
    data_dir = tmp_path / "harness"
    app = create_app(
        HarnessConfig(data_dir=str(data_dir)),
        registry=create_default_registry(include_entry_points=False),
    )

    services = app_services(app)
    owners = services.operational_backends

    assert isinstance(owners.credential_broker, credentials.InMemoryCredentialBroker)
    assert owners.credential_broker.broker_id == "gigaloom-fake-broker-v1"
    sources = owners.credential_broker.list_source_projections()
    assert [item.source_id for item in sources] == ["fake-github-demo"]
    assert len(sources[0].secret_ref_id) == 64
    assert set(sources[0].secret_ref_id) <= set("0123456789abcdef")
    assert isinstance(owners.recovery_receipts, recovery.RecoveryReceiptService)
    assert owners.recovery_receipts.repository is None
    assert isinstance(owners.lane_delta_builder, lane_delta_api.LaneDeltaBuilder)
    assert isinstance(
        owners.lane_delta_store,
        lane_delta_api.FilesystemLaneDeltaPacketStore,
    )
    assert owners.lane_delta_store.root == data_dir / "review" / "lane-deltas-v1"
    assert owners.lane_delta_store.allow_explicit_content is False
    assert isinstance(
        services.session_runner.run_lane_lifecycle,
        lane_delta_api.LaneDeltaLifecycleService,
    )
    assert isinstance(
        owners.visual_artifact_store,
        visual_api.FilesystemVisualArtifactStore,
    )
    assert isinstance(owners.visual_gate_store, visual_api.FilesystemVisualGateStore)
    assert not (data_dir / "automation" / "visual-qa-v1").exists()
