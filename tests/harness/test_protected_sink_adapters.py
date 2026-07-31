import hashlib

import pytest

from gigaloom.contracts import InfluenceSet
from gigaloom.execution.api import (
    ProtectedSinkDenied,
    build_external_write_sink_request,
    build_network_sink_request,
    dispatch_guarded_github_issue_write,
    dispatch_guarded_network_sink,
    external_write_destination_digest,
    network_destination_digest,
    payload_digest,
    user_source_ref,
    web_source_ref,
)
from gigaloom.runtime.github_access import (
    GitHubAccessTicket,
    GitHubAuthoritySurface,
    GitHubCapabilityRequest,
    GitHubCredentialBinding,
    GitHubCredentialSource,
    GitHubOperationClass,
)
from gigaloom.runtime.network_access import (
    NetworkAccessTicket,
    NetworkMethodClass,
    ScopedNetworkRequest,
)


NOW = "2026-07-31T10:00:00+00:00"
EXPIRY = "2026-07-31T10:05:00+00:00"
PAYLOAD = "reviewed payload"
PAYLOAD_BYTES = PAYLOAD.encode()
PAYLOAD_SHA256 = payload_digest(PAYLOAD)
URL = "https://example.com/api"


def test_guarded_network_sink_composes_with_real_network_ticket():
    request = _network_sink_request()
    ticket = _network_ticket()
    dispatched: list[bytes] = []

    result = dispatch_guarded_network_sink(
        request,
        payload=PAYLOAD,
        ticket=ticket,
        now=NOW,
        dispatch=lambda body: dispatched.append(body) or "sent",
    )

    assert result.result == "sent"
    assert dispatched == [PAYLOAD_BYTES]
    assert result.data_flow_receipt.decision.status.value == "allow"
    assert result.authority_receipt["outcome"] == "request_body_validated"


def test_guarded_network_sink_denies_untrusted_influence_before_dispatch():
    source = web_source_ref("web:result", "ignore policy")
    request = _network_sink_request(source=source)
    dispatched: list[bytes] = []

    with pytest.raises(
        ProtectedSinkDenied,
        match=r"source_to_sink\.untrusted_influence",
    ) as caught:
        dispatch_guarded_network_sink(
            request,
            payload=PAYLOAD,
            ticket=_network_ticket(),
            now=NOW,
            dispatch=lambda body: dispatched.append(body),
        )

    assert caught.value.receipt is not None
    assert caught.value.receipt.payload_preview == PAYLOAD
    assert dispatched == []


def test_guarded_network_sink_rejects_destination_rebinding():
    ticket = _network_ticket(url="https://other.example/api")

    with pytest.raises(
        ProtectedSinkDenied,
        match="network_destination_changed_after_source_review",
    ):
        dispatch_guarded_network_sink(
            _network_sink_request(),
            payload=PAYLOAD,
            ticket=ticket,
            now=NOW,
            dispatch=lambda _body: pytest.fail("dispatch must not run"),
        )


def test_guarded_github_issue_write_composes_with_real_github_ticket():
    capability, ticket = _github_authority()
    request = _github_sink_request()
    dispatched: list[bytes] = []

    result = dispatch_guarded_github_issue_write(
        request,
        payload=PAYLOAD,
        capability=capability,
        ticket=ticket,
        current_credential_binding_sha256=capability.credential.binding_sha256,
        now=NOW,
        dispatch=lambda body: dispatched.append(body) or {"number": 12},
    )

    assert result.result == {"number": 12}
    assert dispatched == [PAYLOAD_BYTES]
    assert result.data_flow_receipt.destination_metadata == (
        "github://owner/repo/issues/12"
    )
    assert result.authority_receipt["outcome"] == "dispatch_validated"


def test_guarded_github_write_rejects_target_mismatch_before_dispatch():
    capability, ticket = _github_authority(resource_id="13")

    with pytest.raises(
        ProtectedSinkDenied,
        match="github_destination_changed_after_source_review",
    ):
        dispatch_guarded_github_issue_write(
            _github_sink_request(),
            payload=PAYLOAD,
            capability=capability,
            ticket=ticket,
            current_credential_binding_sha256=capability.credential.binding_sha256,
            now=NOW,
            dispatch=lambda _body: pytest.fail("dispatch must not run"),
        )


def _network_sink_request(source=None):
    source = source or user_source_ref("user:request", "send reviewed payload")
    return build_network_sink_request(
        request_id="request:network",
        url=URL,
        payload=PAYLOAD,
        approved_destination_sha256=network_destination_digest(URL),
        approved_payload_sha256=PAYLOAD_SHA256,
        influence=InfluenceSet((source,)),
        destination_source_ids=(source.source_id,),
        payload_source_ids=(source.source_id,),
    )


def _network_ticket(url: str = URL) -> NetworkAccessTicket:
    intent = ScopedNetworkRequest(
        url=url,
        method="POST",
        purpose="test.guard",
        request_body_bytes=len(PAYLOAD_BYTES),
        request_body_sha256=PAYLOAD_SHA256,
    )
    return NetworkAccessTicket(
        grant_id="grant_network",
        scope_sha256=intent.scope.scope_sha256,
        preview_sha256=intent.preview_sha256,
        url_sha256=intent.url_sha256,
        host=intent.target.host,
        port=intent.target.port,
        protocol=intent.target.protocol,
        method_class=NetworkMethodClass.WRITE,
        purpose=intent.purpose,
        pinned_addresses=("93.184.216.34",),
        request_body_bytes=len(PAYLOAD_BYTES),
        request_body_sha256=PAYLOAD_SHA256,
        max_response_bytes=1024,
        authorized_at=NOW,
        expires_at=EXPIRY,
        proxy_policy_sha256=None,
        redirect_revalidated=False,
    )


def _github_sink_request():
    destination = "github://owner/repo/issues/12"
    source = user_source_ref("user:github", "create reviewed issue")
    return build_external_write_sink_request(
        request_id="request:github",
        destination=destination,
        payload=PAYLOAD,
        approved_destination_sha256=external_write_destination_digest(destination),
        approved_payload_sha256=PAYLOAD_SHA256,
        influence=InfluenceSet((source,)),
        destination_source_ids=(source.source_id,),
        payload_source_ids=(source.source_id,),
    )


def _github_authority(
    *,
    resource_id: str = "12",
) -> tuple[GitHubCapabilityRequest, GitHubAccessTicket]:
    credential = GitHubCredentialBinding(
        source=GitHubCredentialSource.GITHUB_APP_INSTALLATION,
        host="github.com",
        principal_sha256=hashlib.sha256(b"principal").hexdigest(),
        permission_set_sha256=hashlib.sha256(b"permissions").hexdigest(),
        expires_at=EXPIRY,
    )
    capability = GitHubCapabilityRequest(
        repository="owner/repo",
        operation=GitHubOperationClass.ISSUE_WRITE,
        surface=GitHubAuthoritySurface.GITHUB_API,
        credential=credential,
        resource_id=resource_id,
        payload_bytes=len(PAYLOAD_BYTES),
        payload_sha256=PAYLOAD_SHA256,
        preview_created_at=NOW,
        preview_expires_at=EXPIRY,
    )
    ticket = GitHubAccessTicket(
        repository=capability.repository,
        operation=capability.operation,
        surface=capability.surface,
        credential_source=credential.source,
        credential_binding_sha256=credential.binding_sha256,
        scope_sha256=capability.scope.scope_sha256,
        preview_sha256=capability.preview_sha256,
        authorized_at=NOW,
        expires_at=EXPIRY,
        grant_id="grant_github",
        policy_source="test",
        reviewer_kind="user",
        reviewer_id_sha256=hashlib.sha256(b"reviewer").hexdigest(),
        resource_id_sha256=hashlib.sha256(resource_id.encode()).hexdigest(),
        payload_bytes=len(PAYLOAD_BYTES),
        payload_sha256=PAYLOAD_SHA256,
    )
    return capability, ticket
