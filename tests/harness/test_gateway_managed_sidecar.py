"""Managed gateway lifecycle composed on the existing native process owner."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from gigaloom.native.api import (
    GatewayArtifactEvidenceV1,
    GatewayMode,
    GatewayProfileV1,
    GatewaySidecarReason,
    GatewaySidecarStatus,
    ManagedGatewaySidecarService,
    UrlLibGatewayStartupReadinessProbe,
)
from gigaloom.native.base import NativeCommandPlan
from gigaloom.native.process import NativeProcessRef, NativeProcessStatus


class FakeProcessOwner:
    def __init__(self) -> None:
        self.plans: list[tuple[NativeCommandPlan, str, str | None]] = []
        self.refs: dict[str, NativeProcessRef] = {}
        self.stopped: list[str] = []

    def start(
        self,
        plan: NativeCommandPlan,
        *,
        session_id: str,
        workspace: str | None = None,
        run_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> NativeProcessRef:
        del workspace, timeout_seconds
        process_id = f"proc_{len(self.refs) + 1}"
        ref = _process_ref(process_id, plan, session_id, run_id or process_id)
        self.plans.append((plan, session_id, run_id))
        self.refs[process_id] = ref
        return ref

    def status(self, process_id: str) -> NativeProcessRef:
        return self.refs[process_id]

    def stop(self, process_id: str) -> NativeProcessRef:
        self.stopped.append(process_id)
        current = self.refs[process_id]
        stopped = replace(current, status=NativeProcessStatus.STOPPED)
        self.refs[process_id] = stopped
        return stopped


class FakeReadiness:
    def __init__(self, *states: bool) -> None:
        self.states = list(states or (True,))
        self.calls: list[str] = []

    def startup_ready(self, base_url: str) -> bool:
        self.calls.append(base_url)
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]


def _profile() -> GatewayProfileV1:
    return GatewayProfileV1(
        gateway_id="gpt2giga",
        display_name="gpt2giga 0.3",
        mode=GatewayMode.MANAGED,
        distribution="gpt2giga",
        executable="gpt2giga",
        version="0.3.0",
        version_window=">=0.3.0,<0.4.0",
        artifact_sha256="8" * 64,
        base_url="http://127.0.0.1:8090",
        startup_config_revision="sha256:" + "1" * 64,
        health_contract_revision="gpt2giga.health.v1",
        readiness_contract_revision="gpt2giga.readiness.v1",
        models_contract_revision="openai.models.v1",
        capabilities_contract_revision="gpt2giga.route-support-matrix.v1",
        auth_ref="secret-ref:gigachat",
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _artifact(executable: Path) -> GatewayArtifactEvidenceV1:
    return GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256="8" * 64,
        executable_path=str(executable),
        source="locked-registry:gpt2giga-0.3.0",
        verified=True,
    )


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / "bin" / "gpt2giga"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def test_exact_artifact_starts_on_existing_process_owner_and_writes_safe_config(
    tmp_path: Path,
) -> None:
    owner = FakeProcessOwner()
    readiness = FakeReadiness(False, True)
    service = ManagedGatewaySidecarService(
        owner,
        readiness,
        managed_data_root=tmp_path / "gigaloom-data",
        startup_timeout_seconds=1,
        poll_seconds=0.01,
        sleeper=lambda _seconds: None,
    )
    secret = "top-secret-token"

    result = service.ensure_started(
        _profile(),
        _artifact(_executable(tmp_path)),
        environment={"PATH": "/usr/bin:/bin", "GIGACHAT_ACCESS_TOKEN": secret},
        session_id="session_gateway",
        run_id="run_gateway",
    )

    assert result.status is GatewaySidecarStatus.STARTED
    assert result.process_lease_ref == "native-process:proc_1"
    assert result.readiness_confirmed is True
    assert len(owner.plans) == 1
    plan, session_id, run_id = owner.plans[0]
    assert session_id == "session_gateway"
    assert run_id == "run_gateway"
    assert plan.command[1:] == (
        "--proxy.host",
        "127.0.0.1",
        "--proxy.port",
        "8090",
    )
    assert plan.native_home is not None
    assert ".codex" not in plan.native_home
    assert ".claude" not in plan.native_home
    assert ".gemini" not in plan.native_home
    startup = Path(plan.native_home) / "startup.json"
    payload = json.loads(startup.read_text(encoding="utf-8"))
    assert payload["artifact_sha256"] == "8" * 64
    assert payload["environment"]["GIGACHAT_ACCESS_TOKEN"] == "<redacted>"
    assert secret not in startup.read_text(encoding="utf-8")


def test_healthy_sidecar_is_warm_reused_without_second_spawn(tmp_path: Path) -> None:
    owner = FakeProcessOwner()
    readiness = FakeReadiness(True)
    service = ManagedGatewaySidecarService(
        owner,
        readiness,
        managed_data_root=tmp_path / "data",
    )
    artifact = _artifact(_executable(tmp_path))
    first = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session_first",
        run_id="run_first",
    )
    second = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session_second",
        run_id="run_second",
    )

    assert first.status is GatewaySidecarStatus.STARTED
    assert second.status is GatewaySidecarStatus.REUSED
    assert second.process_lease_ref == first.process_lease_ref
    assert len(owner.plans) == 1


def test_unhealthy_owned_sidecar_is_stopped_before_reconnect(tmp_path: Path) -> None:
    owner = FakeProcessOwner()
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(True, False, True),
        managed_data_root=tmp_path / "data",
    )
    artifact = _artifact(_executable(tmp_path))
    first = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session_first",
        run_id="run_first",
    )
    reconnected = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session_second",
        run_id="run_second",
    )

    assert first.process_lease_ref == "native-process:proc_1"
    assert reconnected.status is GatewaySidecarStatus.STARTED
    assert reconnected.process_lease_ref == "native-process:proc_2"
    assert owner.stopped == ["proc_1"]


def test_missing_session_binding_is_a_content_free_start_refusal(
    tmp_path: Path,
) -> None:
    class MissingSessionOwner(FakeProcessOwner):
        def start(self, *args, **kwargs):
            del args, kwargs
            raise KeyError("secret-session-title")

    service = ManagedGatewaySidecarService(
        MissingSessionOwner(),
        FakeReadiness(True),
        managed_data_root=tmp_path / "data",
    )

    result = service.ensure_started(
        _profile(),
        _artifact(_executable(tmp_path)),
        environment={"PATH": "/usr/bin"},
        session_id="missing-session",
        run_id="run",
    )

    assert result.status is GatewaySidecarStatus.BLOCKED
    assert result.reason is GatewaySidecarReason.PROCESS_START_FAILED
    assert "secret-session-title" not in repr(result)


def test_process_loss_is_visible_and_next_ensure_recovers_with_new_lease(
    tmp_path: Path,
) -> None:
    owner = FakeProcessOwner()
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(True),
        managed_data_root=tmp_path / "data",
    )
    artifact = _artifact(_executable(tmp_path))
    started = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session",
        run_id="run-1",
    )
    assert started.process_lease_ref is not None
    owner.refs["proc_1"] = replace(
        owner.refs["proc_1"],
        status=NativeProcessStatus.EXITED,
    )

    lost = service.status(_profile())
    recovered = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session",
        run_id="run-2",
    )

    assert lost.status is GatewaySidecarStatus.LOST
    assert lost.reason is GatewaySidecarReason.PROCESS_LOST
    assert recovered.status is GatewaySidecarStatus.STARTED
    assert recovered.process_lease_ref == "native-process:proc_2"


def test_unverified_or_drifted_artifact_never_spawns(
    tmp_path: Path,
) -> None:
    owner = FakeProcessOwner()
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(True),
        managed_data_root=tmp_path / "data",
    )
    artifact = _artifact(_executable(tmp_path))

    unverified = service.ensure_started(
        _profile(),
        replace(artifact, verified=False),
        environment={},
        session_id="session",
        run_id="run",
    )
    wrong_distribution = service.ensure_started(
        _profile(),
        replace(artifact, distribution="other-gateway"),
        environment={},
        session_id="session",
        run_id="run",
    )

    assert unverified.reason is GatewaySidecarReason.ARTIFACT_UNVERIFIED
    assert wrong_distribution.reason is GatewaySidecarReason.ARTIFACT_IDENTITY_MISMATCH
    assert owner.plans == []


def test_patch_compatible_artifact_starts_and_is_bound_into_the_lease(
    tmp_path: Path,
) -> None:
    owner = FakeProcessOwner()
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(True),
        managed_data_root=tmp_path / "data",
    )
    observed_digest = "9" * 64
    artifact = replace(
        _artifact(_executable(tmp_path)),
        version="0.3.7",
        artifact_sha256=observed_digest,
        source="registry:pypi/gpt2giga==0.3.7",
    )

    result = service.ensure_started(
        _profile(),
        artifact,
        environment={"PATH": "/usr/bin"},
        session_id="session",
        run_id="run",
    )

    assert result.status is GatewaySidecarStatus.STARTED
    assert result.observed_artifact_sha256 == observed_digest
    assert owner.plans[0][0].metadata["artifact_sha256"] == observed_digest


def test_startup_timeout_stops_only_new_sidecar_lease(tmp_path: Path) -> None:
    owner = FakeProcessOwner()
    ticks = iter((0.0, 0.0, 0.2))
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(False),
        managed_data_root=tmp_path / "data",
        startup_timeout_seconds=0.1,
        poll_seconds=0.05,
        monotonic=lambda: next(ticks),
        sleeper=lambda _seconds: None,
    )

    result = service.ensure_started(
        _profile(),
        _artifact(_executable(tmp_path)),
        environment={"PATH": "/usr/bin"},
        session_id="session",
        run_id="run",
    )

    assert result.status is GatewaySidecarStatus.BLOCKED
    assert result.reason is GatewaySidecarReason.STARTUP_READINESS_TIMEOUT
    assert owner.stopped == ["proc_1"]


def test_managed_gateway_rejects_non_loopback_endpoint_before_spawn(
    tmp_path: Path,
) -> None:
    owner = FakeProcessOwner()
    service = ManagedGatewaySidecarService(
        owner,
        FakeReadiness(True),
        managed_data_root=tmp_path / "data",
    )

    result = service.ensure_started(
        replace(_profile(), base_url="https://gateway.example.com"),
        _artifact(_executable(tmp_path)),
        environment={},
        session_id="session",
        run_id="run",
    )

    assert result.reason is GatewaySidecarReason.MANAGED_ENDPOINT_INVALID
    assert owner.plans == []


def test_default_startup_probe_uses_health_only() -> None:
    class Transport:
        calls: list[str] = []

        def get_json(self, base_url, path, *, timeout_seconds):
            del base_url, timeout_seconds
            self.calls.append(path)
            return 200, None

    transport = Transport()
    probe = UrlLibGatewayStartupReadinessProbe(transport)

    assert probe.startup_ready("http://127.0.0.1:8090") is True
    assert transport.calls == ["/health"]


def _process_ref(
    process_id: str,
    plan: NativeCommandPlan,
    session_id: str,
    run_id: str,
) -> NativeProcessRef:
    return NativeProcessRef(
        id=process_id,
        pid=100,
        harness_id="gpt2giga-gateway",
        session_id=session_id,
        run_id=run_id,
        status=NativeProcessStatus.RUNNING,
        command=plan.command,
        display_command=plan.display_command,
        env={},
        cwd=plan.cwd,
        native_home=plan.native_home,
        transport="pipes",
        started_at="2026-08-04T10:00:00+00:00",
        updated_at="2026-08-04T10:00:00+00:00",
    )
