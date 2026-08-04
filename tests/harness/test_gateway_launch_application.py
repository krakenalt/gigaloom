"""Production one-command gateway composition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from gigaloom.cli_commands import gateway_application as application_module
from gigaloom.cli_commands.gateway_application import inspect_gpt2giga_startup
from gigaloom.cli_commands.gateway_launch import parse_gateway_launch_argv
from gigaloom.config import HarnessConfig
from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayArtifactEvidenceV1,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayMode,
    GatewayRouteCatalogV1,
    GatewayRouteDiscovery,
    GatewaySidecarStatus,
    GatewaySupportStatus,
    ManagedGatewayLeaseV1,
)
from gigaloom.cli_commands.gateway_application import GatewayLaunchApplication
from gigaloom.cli_commands.gateway_compatibility import (
    GatewayAgentCompatibilityDecisionV1,
)
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_WHEEL_SHA256,
    reviewed_gpt2giga_profile,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


def _ready_compatibility(agent_id: str) -> GatewayAgentCompatibilityDecisionV1:
    return GatewayAgentCompatibilityDecisionV1(
        agent_id=agent_id,
        harness_id="codex-cli",
        status="ready",
        reason_id="gateway_agent_compatibility_admitted",
        expected_version_window="==0.146.0",
        observed_version="0.146.0",
    )


class _Discovery:
    def __init__(self, result: GatewayDiscoveryResult) -> None:
        self.result = result
        self.calls: list[bool] = []

    def discover(self, _profile, *, force_refresh=False):
        self.calls.append(force_refresh)
        return self.result


class _Sidecar:
    def __init__(self, profile) -> None:
        self.profile = profile
        self.environments: list[dict[str, str]] = []
        self.stopped = 0
        self.observed_artifact_sha256: str | None = None

    def ensure_started(
        self,
        _profile,
        artifact,
        *,
        environment,
        session_id,
        run_id,
    ):
        assert (session_id, run_id) == ("gateway-launch", "gateway-gpt2giga")
        self.environments.append(dict(environment))
        self.observed_artifact_sha256 = artifact.artifact_sha256
        return ManagedGatewayLeaseV1(
            gateway_id=self.profile.gateway_id,
            profile_digest=self.profile.profile_digest,
            status=GatewaySidecarStatus.STARTED,
            process_lease_ref="native-process:gateway-1",
            managed_root="/managed/gateway",
            startup_config_ref="managed-config:startup.json",
            readiness_confirmed=True,
            observed_artifact_sha256=artifact.artifact_sha256,
        )

    def stop(self, _profile):
        self.stopped += 1
        return ManagedGatewayLeaseV1(
            gateway_id=self.profile.gateway_id,
            profile_digest=self.profile.profile_digest,
            status=GatewaySidecarStatus.STOPPED,
            process_lease_ref="native-process:gateway-1",
            managed_root="/managed/gateway",
            startup_config_ref="managed-config:startup.json",
            readiness_confirmed=False,
            observed_artifact_sha256=self.observed_artifact_sha256,
        )


def _route() -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id="codex-gpt2giga-gigachat-2-max",
        agent_id="codex",
        client_protocol="openai_responses",
        gateway_profile_id="gpt2giga",
        public_model_alias="GigaChat-2-Max",
        upstream_provider="gigachat",
        upstream_model="GigaChat-2-Max",
        capability_profile_revision="sha256:" + "b" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=GatewaySupportStatus.TECHNICAL_PREVIEW,
        reason_ids=("normalized_responses_parity_incomplete",),
        evidence_ids=("COR-01-CODEX-RESPONSES-2026-08-03",),
    )


def _discovery(profile) -> GatewayDiscoveryResult:
    route = _route()
    return GatewayDiscoveryResult(
        GatewayDiscoveryStatus.CURRENT,
        GatewayRouteCatalogV1(
            gateway_id=profile.gateway_id,
            profile_digest=profile.profile_digest,
            models_revision="sha256:" + "d" * 64,
            capabilities_revision="sha256:" + "e" * 64,
            loss_matrix_revision=route.loss_matrix_revision,
            routes=(route,),
            discovered_at=NOW.isoformat(),
            expires_at=(NOW + timedelta(minutes=1)).isoformat(),
            catalog_digest="f" * 64,
        ),
    )


def _artifact(executable: Path) -> GatewayArtifactEvidenceV1:
    return GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256=GPT2GIGA_WHEEL_SHA256,
        executable_path=str(executable),
        source="locked-registry:pypi/gpt2giga==0.3.0",
        verified=True,
    )


def _request(*, dry_run: bool = False, json_output: bool = False):
    arguments = [
        "--with",
        "gpt2giga",
        "--model",
        "GigaChat-2-Max",
    ]
    if dry_run:
        arguments.append("--dry-run")
    if json_output:
        arguments.append("--json")
    arguments.extend(("codex", "--help"))
    request = parse_gateway_launch_argv(arguments)
    assert request is not None
    return request


def _application(tmp_path: Path):
    profile = reviewed_gpt2giga_profile(
        base_url="http://127.0.0.1:8090",
        mode=GatewayMode.MANAGED,
    )
    executable = tmp_path / "gpt2giga"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    discovery = _Discovery(_discovery(profile))
    sidecar = _Sidecar(profile)
    application = GatewayLaunchApplication(
        config=HarnessConfig(data_dir=str(tmp_path)),
        profile=profile,
        discovery=cast(GatewayRouteDiscovery, discovery),
        artifact_resolver=lambda _profile: _artifact(executable),
        managed_root=tmp_path / "managed",
        gateway_api_key="gateway-secret-key",
        compatibility_resolver=_ready_compatibility,
        sidecar=sidecar,
        startup_inspector=lambda *_args: None,
        clock=lambda: NOW,
    )
    return application, discovery, sidecar


def test_managed_launch_composes_artifact_discovery_preflight_overlay_and_native(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application, discovery, sidecar = _application(tmp_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "upstream-secret")
    launched: list[tuple[tuple[str, ...], dict[str, str]]] = []

    exit_code = application.run(
        _request(),
        native_launcher=lambda argv, environment: (
            launched.append((argv, dict(environment))) or 17
        ),
    )

    assert exit_code == 17
    assert discovery.calls == [True]
    assert sidecar.stopped == 1
    assert sidecar.environments[0]["GIGACHAT_CREDENTIALS"] == "upstream-secret"
    argv, environment = launched[0]
    assert argv == ("codex", "--help")
    assert environment["GPT2GIGA_API_KEY"] == "gateway-secret-key"
    assert Path(environment["CODEX_HOME"]).is_relative_to(tmp_path / "managed")
    assert "GIGACHAT_CREDENTIALS" not in environment
    config = Path(environment["CODEX_HOME"]) / "config.toml"
    assert 'wire_api = "responses"' in config.read_text(encoding="utf-8")
    assert "GigaChat-2-Max" in config.read_text(encoding="utf-8")


def test_managed_launch_accepts_a_contract_compatible_patch_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application, _discovery_service, _sidecar = _application(tmp_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "upstream-secret")
    executable = tmp_path / "gpt2giga-patch"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    application.artifact_resolver = lambda _profile: replace(
        _artifact(executable),
        version="0.3.7",
        artifact_sha256="9" * 64,
        source="registry:pypi/gpt2giga==0.3.7",
    )

    exit_code = application.run(
        _request(),
        native_launcher=lambda _argv, _environment: 23,
    )

    assert exit_code == 23


def test_startup_inspection_rejects_an_unknown_machine_contract_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile = reviewed_gpt2giga_profile(
        base_url="http://127.0.0.1:8090",
        mode=GatewayMode.MANAGED,
    )
    executable = tmp_path / "gpt2giga"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        application_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": "gpt2giga.inspect.v2",
                    "profile_schema_version": "gpt2giga.provider-profiles.v2",
                    "valid": True,
                    "config_revision": profile.startup_config_revision,
                    "matrix_revision": "sha256:"
                    + "3cad19e6f7b531e50a0eb4c88af308aee5be81f3a4c085f89ebc2e06f92ffa69",
                }
            ),
        ),
    )

    reason = inspect_gpt2giga_startup(
        profile,
        _artifact(executable),
        {"HOME": str(home)},
    )

    assert reason == "startup_contract_mismatch"


def test_dry_run_is_content_free_and_does_not_start_or_write(
    tmp_path: Path,
    capsys,
) -> None:
    application, discovery, sidecar = _application(tmp_path)
    managed_root = application.managed_root

    def fail_if_probed(_agent_id: str) -> GatewayAgentCompatibilityDecisionV1:
        raise AssertionError("dry-run must not execute an installed-agent probe")

    application.compatibility_resolver = fail_if_probed

    assert (
        application.run(
            _request(dry_run=True, json_output=True),
            native_launcher=lambda *_args: 99,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["artifact_state"] == "verified"
    assert payload["process_spawn"] is False
    assert discovery.calls == [True]
    assert sidecar.environments == []
    assert sidecar.stopped == 0
    assert not managed_root.exists()


def test_agent_compatibility_refusal_precedes_sidecar_and_native_handoff(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    application, discovery, sidecar = _application(tmp_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "upstream-secret")
    application.compatibility_resolver = lambda agent_id: (
        GatewayAgentCompatibilityDecisionV1(
            agent_id=agent_id,
            harness_id="codex-cli",
            status="blocked",
            reason_id="gateway_agent_version_outside_reviewed_window",
            expected_version_window="==0.146.0",
            observed_version="0.147.0",
        )
    )
    artifacts: list[object] = []
    application.artifact_resolver = lambda _profile: artifacts.append(object()) or None
    launched: list[object] = []

    assert (
        application.run(
            _request(json_output=True),
            native_launcher=lambda *_args: launched.append(object()) or 0,
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["reason_ids"] == ["gateway_agent_version_outside_reviewed_window"]
    assert payload["expected_version_window"] == "==0.146.0"
    assert payload["observed_version"] == "0.147.0"
    assert payload["fallback_allowed"] is False
    assert payload["provider_traffic"] is False
    assert payload["process_spawn"] is False
    assert artifacts == []
    assert discovery.calls == []
    assert sidecar.environments == []
    assert sidecar.stopped == 0
    assert launched == []


def test_resolution_refusal_stops_the_sidecar_before_native_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application, discovery, sidecar = _application(tmp_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "upstream-secret")
    request = parse_gateway_launch_argv(
        [
            "--with",
            "gpt2giga",
            "--model",
            "not-advertised",
            "codex",
        ]
    )
    assert request is not None
    launched: list[object] = []

    assert (
        application.run(
            request,
            native_launcher=lambda *_args: launched.append(object()) or 0,
        )
        == 2
    )

    assert discovery.calls == [True]
    assert sidecar.stopped == 1
    assert launched == []
