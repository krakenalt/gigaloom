from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from gigaloom.tools.mcp.apps import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_SPEC_VERSION,
    MCP_APP_HTML_MIME_TYPE,
    MCPAppChannelError,
    MCPAppDisplayContext,
    MCPAppFallbackCode,
    MCPAppFrameBinding,
    MCPAppLimits,
    MCPAppResourceCache,
    MCPAppResourceCandidate,
    MCPAppServerIdentity,
    MCPAppSessionRegistry,
    admit_mcp_app_resource,
    mcp_app_resource_headers,
)
from gigaloom.ui.routers.mcp_apps import create_router
from gigaloom.ui.services.mcp_apps import MCPAppHostService

FIXTURES = Path(__file__).parent / "fixtures" / "mcp_apps"


def _html(name: str = "trusted_compatibility_preview.html") -> bytes:
    return (FIXTURES / name).read_bytes()


def _candidate(
    *,
    html: bytes | None = None,
    server: MCPAppServerIdentity | None = None,
    uri: str = "ui://local-app/compatibility-preview",
    mime_type: str = MCP_APP_HTML_MIME_TYPE,
    digest: str | None = None,
    textual_fallback: str = "Compatibility preview is available as structured data.",
    structured_fallback: dict[str, object] | None = None,
    specification_version: str = MCP_APPS_SPEC_VERSION,
    extension_id: str = MCP_APPS_EXTENSION_ID,
    requested_permissions: tuple[str, ...] = (),
    requested_connect_domains: tuple[str, ...] = (),
) -> MCPAppResourceCandidate:
    content = _html() if html is None else html
    return MCPAppResourceCandidate(
        server=server or MCPAppServerIdentity("local-app", True, True, True),
        uri=uri,
        mime_type=mime_type,
        html=content,
        expected_sha256=digest or hashlib.sha256(content).hexdigest(),
        textual_fallback=textual_fallback,
        structured_fallback=structured_fallback or {"status": "fallback"},
        specification_version=specification_version,
        extension_id=extension_id,
        requested_permissions=requested_permissions,
        requested_connect_domains=requested_connect_domains,
    )


def _admitted(candidate: MCPAppResourceCandidate | None = None):
    admission = admit_mcp_app_resource(candidate or _candidate())
    assert admission.fallback is None
    assert admission.resource is not None
    return admission.resource


def _binding(resource=None, **overrides: str) -> MCPAppFrameBinding:
    resource = resource or _admitted()
    values = {
        "server_id": resource.server_id,
        "tool_id": "agent.compatibility.preview",
        "resource_sha256": resource.sha256,
        "workspace_id": "workspace_1",
        "session_id": "session_1",
        "run_id": "run_1",
        **overrides,
    }
    return MCPAppFrameBinding(**values)


def _message(
    descriptor,
    *,
    request_id: str | int = 1,
    method: str = "ui/ready",
    params: dict[str, object] | None = None,
    channel_id: str | None = None,
    nonce: str | None = None,
) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
            "channelId": channel_id or descriptor.channel_id,
            "nonce": nonce or descriptor.nonce,
        },
        separators=(",", ":"),
    ).encode()


def _client(service: MCPAppHostService) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(service))
    return TestClient(app)


def test_admits_exact_pinned_local_content_addressed_resource():
    candidate = _candidate()

    resource = _admitted(candidate)

    assert resource.server_id == "local-app"
    assert resource.uri == "ui://local-app/compatibility-preview"
    assert resource.mime_type == MCP_APP_HTML_MIME_TYPE
    assert resource.sha256 == hashlib.sha256(candidate.html).hexdigest()
    assert resource.size_bytes == len(candidate.html)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"specification_version": "2025-11-21"}, "unsupported_specification"),
        ({"extension_id": "ui"}, "unsupported_extension"),
        ({"mime_type": "text/html"}, "invalid_mime_type"),
        ({"digest": "0" * 64}, "digest_mismatch"),
        ({"digest": "ABC"}, "invalid_digest"),
        ({"html": b"\xff"}, "invalid_html"),
    ],
)
def test_drifted_or_invalid_resource_returns_typed_fallback(changes, code):
    admission = admit_mcp_app_resource(_candidate(**changes))

    assert admission.resource is None
    assert admission.fallback is not None
    assert admission.fallback.code.value == code
    assert admission.fallback.textual
    assert admission.fallback.structured == {"status": "fallback"}


@pytest.mark.parametrize(
    ("server", "code"),
    [
        (MCPAppServerIdentity("local-app", False, True, True), "server_not_local"),
        (MCPAppServerIdentity("local-app", True, False, True), "server_not_trusted"),
        (MCPAppServerIdentity("local-app", True, True, False), "server_not_read_only"),
    ],
)
def test_only_trusted_local_read_only_server_is_admitted(server, code):
    admission = admit_mcp_app_resource(_candidate(server=server))

    assert admission.fallback is not None
    assert admission.fallback.code.value == code


@pytest.mark.parametrize(
    "uri",
    [
        "http://local-app/resource",
        "ui://other-server/resource",
        "ui://local-app/../resource",
        "ui://local-app/%2e%2e/resource",
        "ui://local-app//resource",
        "ui://local-app/resource?remote=true",
        "ui://local-app/resource#fragment",
        "ui://local-app:999/resource",
        "ui://local-app/",
        "ui://[invalid/resource",
    ],
)
def test_rejects_noncanonical_or_cross_server_ui_uri(uri):
    admission = admit_mcp_app_resource(_candidate(uri=uri))

    assert admission.fallback is not None
    assert admission.fallback.code is MCPAppFallbackCode.INVALID_URI


def test_resource_and_fallback_limits_fail_closed_with_bounded_replacement():
    html = b"<p>too large</p>"
    resource_denial = admit_mcp_app_resource(
        _candidate(html=html),
        limits=MCPAppLimits(max_html_resource_bytes=4),
    )
    fallback_denial = admit_mcp_app_resource(
        _candidate(textual_fallback="too large"),
        limits=MCPAppLimits(max_app_instance_state_bytes=4),
    )

    assert resource_denial.fallback is not None
    assert resource_denial.fallback.code is MCPAppFallbackCode.RESOURCE_TOO_LARGE
    assert fallback_denial.fallback is not None
    assert fallback_denial.fallback.code is MCPAppFallbackCode.FALLBACK_TOO_LARGE
    assert fallback_denial.fallback.textual == "!"
    assert fallback_denial.fallback.structured == {}


def test_cache_enforces_server_count_workspace_bytes_and_server_binding():
    first = _admitted(_candidate(html=b"<p>one</p>", uri="ui://local-app/one"))
    second = _admitted(_candidate(html=b"<p>two</p>", uri="ui://local-app/two"))
    count_cache = MCPAppResourceCache(
        limits=MCPAppLimits(max_cached_resources_per_server=1)
    )
    count_cache.put(first)

    with pytest.raises(ValueError, match="server resource cache limit"):
        count_cache.put(second)

    byte_cache = MCPAppResourceCache(
        limits=MCPAppLimits(max_workspace_cache_bytes=first.size_bytes)
    )
    byte_cache.put(first)
    with pytest.raises(ValueError, match="workspace cache byte limit"):
        byte_cache.put(second)
    assert byte_cache.get("other-server", first.sha256) is None
    assert byte_cache.snapshot().total_bytes == first.size_bytes


@pytest.mark.parametrize(
    "fixture_name",
    ["malicious_embeds.html", "csp_relaxation.html"],
)
def test_active_embeds_base_remote_assets_and_csp_relaxation_use_fallback(
    fixture_name,
):
    admission = admit_mcp_app_resource(_candidate(html=_html(fixture_name)))

    assert admission.fallback is not None
    assert admission.fallback.code is MCPAppFallbackCode.POLICY_DENIED
    assert admission.fallback.denied_evidence


def test_requested_domains_and_permissions_are_denied_and_evidence_is_bounded():
    admission = admit_mcp_app_resource(
        _candidate(
            requested_permissions=("camera", "microphone"),
            requested_connect_domains=("https://evil.invalid",),
        )
    )
    excessive = admit_mcp_app_resource(
        _candidate(
            requested_permissions=tuple(f"{index}-" + "x" * 500 for index in range(40))
        )
    )

    assert admission.fallback is not None
    assert admission.fallback.denied_evidence == (
        "denied_connect_domain:https://evil.invalid",
        "denied_permission:camera",
        "denied_permission:microphone",
    )
    assert excessive.fallback is not None
    assert "requested_permissions_limit" in excessive.fallback.denied_evidence
    assert len(excessive.fallback.denied_evidence) == 33
    assert max(map(len, excessive.fallback.denied_evidence)) < 160


@pytest.mark.parametrize(
    "fixture_name",
    ["attempt_parent_storage.html", "attempt_network.html"],
)
def test_hostile_script_is_contained_by_opaque_sandbox_and_no_network_csp(
    fixture_name,
):
    resource = _admitted(_candidate(html=_html(fixture_name)))
    registry = MCPAppSessionRegistry()

    descriptor = registry.create(resource, _binding(resource))

    assert descriptor.sandbox == "allow-scripts"
    assert "allow-same-origin" not in descriptor.sandbox
    assert "default-src 'none'" in descriptor.content_security_policy
    assert "connect-src 'none'" in descriptor.content_security_policy
    assert "frame-src 'none'" in descriptor.content_security_policy
    assert "img-src 'none'" in descriptor.content_security_policy
    headers = mcp_app_resource_headers()
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "camera=()" in headers["Permissions-Policy"]
    assert not any(key.lower().startswith("access-control") for key in headers)


def test_initialization_contains_only_channel_and_bounded_display_metadata():
    resource = _admitted()
    descriptor = MCPAppSessionRegistry().create(
        resource,
        _binding(resource),
        display=MCPAppDisplayContext(theme="dark", locale="ru", display_mode="panel"),
    )

    assert descriptor.initialization == {
        "protocolVersion": MCP_APPS_SPEC_VERSION,
        "channelId": descriptor.channel_id,
        "nonce": descriptor.nonce,
        "display": {"theme": "dark", "locale": "ru", "mode": "panel"},
    }
    serialized = json.dumps(descriptor.initialization)
    assert "workspace_1" not in serialized
    assert "session_1" not in serialized
    assert "run_1" not in serialized
    assert "token" not in serialized.lower()


@pytest.mark.parametrize(
    ("source", "message_change", "code"),
    [
        ("forged", {}, "source_mismatch"),
        (None, {"channel_id": "forged"}, "channel_mismatch"),
        (None, {"nonce": "forged"}, "nonce_mismatch"),
        (None, {"method": "tools/call"}, "method_denied"),
        (None, {"method": "openLink"}, "method_denied"),
        (
            None,
            {"method": "ui/userChoice", "params": {"choiceId": "x", "extra": 1}},
            "invalid_params",
        ),
    ],
)
def test_bridge_rejects_forged_channels_privileged_methods_and_untyped_choice(
    source,
    message_change,
    code,
):
    resource = _admitted()
    registry = MCPAppSessionRegistry()
    descriptor = registry.create(resource, _binding(resource))
    bridge = registry.get_bridge(descriptor.instance_id)
    assert bridge is not None

    with pytest.raises(MCPAppChannelError) as caught:
        bridge.accept(
            source_id=source or descriptor.source_id,
            raw_message=_message(descriptor, **message_change),
        )

    assert caught.value.code == code


def test_bridge_rejects_replay_outstanding_overflow_and_oversized_payload():
    resource = _admitted()
    limits = MCPAppLimits(max_outstanding_app_requests=1, max_post_message_bytes=512)
    registry = MCPAppSessionRegistry(limits=limits)
    descriptor = registry.create(resource, _binding(resource))
    bridge = registry.get_bridge(descriptor.instance_id)
    assert bridge is not None
    first = _message(descriptor, request_id="first")
    bridge.accept(source_id=descriptor.source_id, raw_message=first)

    with pytest.raises(MCPAppChannelError, match="outstanding") as outstanding:
        bridge.accept(
            source_id=descriptor.source_id,
            raw_message=_message(descriptor, request_id="second"),
        )
    assert outstanding.value.code == "outstanding_limit"
    assert bridge.complete("first")
    with pytest.raises(MCPAppChannelError, match="already been used") as replay:
        bridge.accept(source_id=descriptor.source_id, raw_message=first)
    assert replay.value.code == "replayed_request"
    with pytest.raises(MCPAppChannelError, match="byte limit") as oversized:
        bridge.accept(source_id=descriptor.source_id, raw_message=b"x" * 513)
    assert oversized.value.code == "payload_limit"


def test_bridge_request_history_is_bounded_without_permitting_old_replay():
    resource = _admitted()
    registry = MCPAppSessionRegistry(
        limits=MCPAppLimits(max_outstanding_app_requests=1)
    )
    descriptor = registry.create(resource, _binding(resource))
    bridge = registry.get_bridge(descriptor.instance_id)
    assert bridge is not None
    for request_id in range(256):
        bridge.accept(
            source_id=descriptor.source_id,
            raw_message=_message(descriptor, request_id=request_id),
        )
        assert bridge.complete(request_id)

    with pytest.raises(MCPAppChannelError) as caught:
        bridge.accept(
            source_id=descriptor.source_id,
            raw_message=_message(descriptor, request_id=256),
        )

    assert caught.value.code == "request_history_limit"


def test_teardown_cancels_pending_ignores_late_response_and_audits_no_raw_id():
    resource = _admitted()
    registry = MCPAppSessionRegistry()
    descriptor = registry.create(resource, _binding(resource))
    bridge = registry.get_bridge(descriptor.instance_id)
    assert bridge is not None
    secret_request_id = "raw-secret-request-id"
    bridge.accept(
        source_id=descriptor.source_id,
        raw_message=_message(descriptor, request_id=secret_request_id),
    )

    assert registry.teardown(descriptor.instance_id) == (secret_request_id,)
    assert bridge.complete(secret_request_id) is False
    evidence = registry.evidence.snapshot()
    assert evidence[-1].code == "late_response"
    assert evidence[-1].request_id_digest is not None
    assert len(evidence[-1].request_id_digest or "") == 64
    assert secret_request_id not in repr(evidence)


def test_frame_binding_cannot_cross_server_or_resource_digest():
    resource = _admitted()
    registry = MCPAppSessionRegistry()

    with pytest.raises(MCPAppChannelError) as caught:
        registry.create(resource, _binding(resource, server_id="other-server"))

    assert caught.value.code == "binding_mismatch"


def test_host_service_returns_fallback_for_missing_resource_and_instance_limit():
    limits = MCPAppLimits()
    sessions = MCPAppSessionRegistry(limits=limits, max_instances=1)
    service = MCPAppHostService(limits=limits, sessions=sessions)
    resource = _admitted()
    assert service.admit(_candidate()) is None
    missing = service.create_frame(
        replace(_binding(resource), resource_sha256="0" * 64)
    )
    first = service.create_frame(_binding(resource))
    second = service.create_frame(_binding(resource))

    assert missing.fallback is not None
    assert missing.fallback.code is MCPAppFallbackCode.RESOURCE_UNAVAILABLE
    assert first.descriptor is not None
    assert second.fallback is not None
    assert second.fallback.code is MCPAppFallbackCode.HOST_LIMIT


def test_backend_router_returns_descriptor_resource_choice_and_teardown():
    service = MCPAppHostService()
    candidate = _candidate()
    assert service.admit(candidate) is None
    client = _client(service)
    payload = {
        "server_id": "local-app",
        "tool_id": "agent.compatibility.preview",
        "resource_sha256": candidate.expected_sha256,
        "workspace_id": "workspace_1",
        "session_id": "session_1",
        "run_id": "run_1",
    }

    created = client.post("/api/mcp-apps/frames", json=payload)

    assert created.status_code == 200
    frame = created.json()["frame"]
    assert created.json()["status"] == "admitted"
    assert frame["sandbox"] == "allow-scripts"
    resource = client.get(frame["resource_url"])
    assert resource.content == candidate.html
    assert resource.headers["content-type"] == MCP_APP_HTML_MIME_TYPE
    assert (
        resource.headers["content-security-policy"] == frame["content_security_policy"]
    )
    message = json.loads(
        _message(
            type("Descriptor", (), frame),
            method="ui/userChoice",
            params={"choiceId": "codex"},
        )
    )
    accepted = client.post(
        f"/api/mcp-apps/frames/{frame['instance_id']}/messages",
        json=message,
        headers={"X-GigaLoom-MCP-App-Source": frame["source_id"]},
    )
    assert accepted.status_code == 200
    assert service.choice(frame["instance_id"]) == "codex"
    destroyed = client.delete(f"/api/mcp-apps/frames/{frame['instance_id']}")
    assert destroyed.json() == {"destroyed": True, "cancelled_requests": 0}
    assert client.get(frame["resource_url"]).status_code == 404


def test_backend_router_returns_typed_fallback_and_security_errors():
    service = MCPAppHostService(limits=MCPAppLimits(max_post_message_bytes=512))
    client = _client(service)
    missing = client.post(
        "/api/mcp-apps/frames",
        json={
            "server_id": "local-app",
            "tool_id": "agent.compatibility.preview",
            "resource_sha256": "0" * 64,
            "workspace_id": "workspace_1",
            "session_id": "session_1",
            "run_id": "run_1",
        },
    )
    candidate = _candidate(html=b"<p>safe</p>")
    assert service.admit(candidate) is None
    created = client.post(
        "/api/mcp-apps/frames",
        json={
            "server_id": "local-app",
            "tool_id": "agent.compatibility.preview",
            "resource_sha256": candidate.expected_sha256,
            "workspace_id": "workspace_1",
            "session_id": "session_1",
            "run_id": "run_1",
        },
    ).json()["frame"]
    forged = client.post(
        f"/api/mcp-apps/frames/{created['instance_id']}/messages",
        content=_message(type("Descriptor", (), created)),
        headers={"X-GigaLoom-MCP-App-Source": "forged"},
    )
    oversized = client.post(
        f"/api/mcp-apps/frames/{created['instance_id']}/messages",
        content=b"x" * 513,
        headers={"X-GigaLoom-MCP-App-Source": created["source_id"]},
    )

    assert missing.json()["fallback"]["code"] == "resource_unavailable"
    assert forged.status_code == 403
    assert forged.json()["detail"]["code"] == "source_mismatch"
    assert oversized.status_code == 413


def test_cached_frame_descriptor_backend_budget_is_below_100ms():
    service = MCPAppHostService()
    resource = _admitted()
    assert service.admit(_candidate()) is None
    outcome = service.create_frame(_binding(resource))
    assert outcome.descriptor is not None

    started = perf_counter()
    for _iteration in range(200):
        assert service.descriptor(outcome.descriptor.instance_id) is outcome.descriptor
    average_seconds = (perf_counter() - started) / 200

    assert average_seconds < 0.1
