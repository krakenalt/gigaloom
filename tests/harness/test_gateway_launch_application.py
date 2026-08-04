"""Production one-command gateway composition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import cast

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
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_WHEEL_SHA256,
    reviewed_gpt2giga_profile,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


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

    def ensure_started(
        self,
        _profile,
        _artifact,
        *,
        environment,
        session_id,
        run_id,
    ):
        assert (session_id, run_id) == ("gateway-launch", "gateway-gpt2giga")
        self.environments.append(dict(environment))
        return ManagedGatewayLeaseV1(
            gateway_id=self.profile.gateway_id,
            profile_digest=self.profile.profile_digest,
            status=GatewaySidecarStatus.STARTED,
            process_lease_ref="native-process:gateway-1",
            managed_root="/managed/gateway",
            startup_config_ref="managed-config:startup.json",
            readiness_confirmed=True,
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


def test_dry_run_is_content_free_and_does_not_start_or_write(
    tmp_path: Path,
    capsys,
) -> None:
    application, discovery, sidecar = _application(tmp_path)
    managed_root = application.managed_root

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
