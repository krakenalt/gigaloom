"""Draft ACP configurable-provider capability and method contracts."""

from __future__ import annotations

from pathlib import Path
import queue
from typing import Any, Mapping

import pytest

from gigaloom.harnesses.acp import (
    AcpRouteIdentity,
    configure_provider,
    create_acp_client,
    disable_provider,
    list_providers,
    pin_acp_process,
)
from gigaloom.harnesses.acp.errors import AcpCapabilityError, AcpProtocolError
from gigaloom.structured_processes import StructuredTransportClosed


_DIGEST = "d" * 64
_CLOSED = object()


class _ProviderTransport:
    def __init__(
        self,
        *,
        providers_capability: bool = True,
        providers: list[dict[str, Any]] | None = None,
        after_set: list[dict[str, Any]] | None = None,
        malformed: Mapping[str, Any] | None = None,
        reject_set: bool = False,
    ) -> None:
        self.providers_capability = providers_capability
        self.providers = providers if providers is not None else [_provider()]
        self.after_set = after_set
        self.malformed = malformed
        self.reject_set = reject_set
        self.incoming: queue.Queue[Mapping[str, Any] | object] = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self._alive = False
        self._set = False

    @property
    def runtime_id(self) -> str:
        return "fake-acp-providers"

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self) -> None:
        self._alive = True

    def send(self, payload: Mapping[str, Any]) -> None:
        message = dict(payload)
        self.sent.append(message)
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None or method is None:
            return
        if method == "initialize":
            capabilities: dict[str, Any] = {"promptCapabilities": {}}
            if self.providers_capability:
                capabilities["providers"] = {}
            self._success(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "provider-agent", "version": "1.0.0"},
                    "agentCapabilities": capabilities,
                },
            )
        elif method == "providers/list":
            if self.malformed is not None:
                self._success(request_id, dict(self.malformed))
            else:
                providers = (
                    self.after_set
                    if self._set and self.after_set is not None
                    else self.providers
                )
                self._success(request_id, {"providers": providers})
        elif method == "providers/set":
            if self.reject_set:
                self.incoming.put(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "rejected"},
                    }
                )
            else:
                self._set = True
                self._success(request_id, {})
        elif method == "providers/disable":
            self._success(request_id, {})

    def receive(self, timeout: float) -> Mapping[str, Any] | None:
        try:
            value = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        if value is _CLOSED:
            raise StructuredTransportClosed("closed")
        return value  # type: ignore[return-value]

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False

    def wait(self, timeout: float) -> int:
        del timeout
        return 0

    def _success(self, request_id: object, result: Mapping[str, Any]) -> None:
        self.incoming.put({"jsonrpc": "2.0", "id": request_id, "result": result})


def _provider(
    provider_id: str = "main",
    *,
    supported: list[str] | None = None,
    required: bool = True,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "supported": supported or ["openai"],
        "required": required,
        "current": current,
    }


def _client(tmp_path: Path, transport: _ProviderTransport):
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "fake-provider-agent"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    spec = pin_acp_process((executable.as_posix(),), cwd=tmp_path, environment={})
    client = create_acp_client(
        spec,
        compatibility_profile_digest=_DIGEST,
        route_identity=AcpRouteIdentity("provider-agent", "provider.acp", _DIGEST),
        transport_factory=lambda: transport,
    )
    client.start()
    client.initialize()
    return client


def test_configure_provider_is_capability_gated_bounded_and_verified(
    tmp_path: Path,
) -> None:
    route = "http://127.0.0.1:8090/v1"
    transport = _ProviderTransport(
        after_set=[_provider(current={"apiType": "openai", "baseUrl": route})]
    )
    client = _client(tmp_path, transport)

    selected = configure_provider(
        client,
        api_type="openai",
        base_url=route,
        headers={"Authorization": "Bearer ephemeral-secret"},
    )

    assert selected.provider_id == "main"
    assert selected.current_api_type == "openai"
    assert selected.current_base_url == route
    assert [item["method"] for item in transport.sent] == [
        "initialize",
        "providers/list",
        "providers/set",
        "providers/list",
    ]
    assert transport.sent[2]["params"] == {
        "id": "main",
        "apiType": "openai",
        "baseUrl": route,
        "headers": {"Authorization": "Bearer ephemeral-secret"},
    }
    assert "ephemeral-secret" not in repr(selected)


def test_provider_methods_reject_absent_capability_and_malformed_list(
    tmp_path: Path,
) -> None:
    absent_transport = _ProviderTransport(providers_capability=False)
    absent = _client(tmp_path / "absent", absent_transport)
    with pytest.raises(AcpCapabilityError, match="not negotiated"):
        list_providers(absent)
    assert [item["method"] for item in absent_transport.sent] == ["initialize"]

    malformed_transport = _ProviderTransport(malformed={"providers": [{"id": "x"}]})
    malformed = _client(tmp_path / "malformed", malformed_transport)
    with pytest.raises(AcpProtocolError, match="schema validation"):
        list_providers(malformed)


@pytest.mark.parametrize(
    ("providers", "error", "message"),
    [
        (
            [_provider("a"), _provider("b")],
            AcpProtocolError,
            "ambiguous",
        ),
        (
            [_provider(supported=["anthropic"])],
            AcpCapabilityError,
            "not supported",
        ),
    ],
)
def test_configure_provider_rejects_ambiguous_or_unknown_protocol(
    tmp_path: Path,
    providers: list[dict[str, Any]],
    error: type[Exception],
    message: str,
) -> None:
    client = _client(tmp_path, _ProviderTransport(providers=providers))
    with pytest.raises(error, match=message):
        configure_provider(
            client,
            api_type="openai",
            base_url="http://127.0.0.1:8090/v1",
        )


def test_configure_provider_fails_closed_on_rejection_or_post_set_mismatch(
    tmp_path: Path,
) -> None:
    rejected = _client(tmp_path / "rejected", _ProviderTransport(reject_set=True))
    with pytest.raises(Exception) as rejected_error:
        configure_provider(
            rejected,
            api_type="openai",
            base_url="http://127.0.0.1:8090/v1",
            headers={"Authorization": "Bearer must-not-leak"},
        )
    assert "must-not-leak" not in str(rejected_error.value)

    mismatched = _client(
        tmp_path / "mismatched",
        _ProviderTransport(
            after_set=[
                _provider(
                    current={
                        "apiType": "openai",
                        "baseUrl": "http://wrong.invalid/v1",
                    }
                )
            ]
        ),
    )
    with pytest.raises(AcpProtocolError, match="was not applied"):
        configure_provider(
            mismatched,
            api_type="openai",
            base_url="http://127.0.0.1:8090/v1",
        )


def test_disable_provider_rejects_required_and_uses_draft_method(
    tmp_path: Path,
) -> None:
    required = _client(tmp_path / "required", _ProviderTransport())
    with pytest.raises(AcpCapabilityError, match="required"):
        disable_provider(required, provider_id="main")

    transport = _ProviderTransport(providers=[_provider(required=False)])
    optional = _client(tmp_path / "optional", transport)
    disable_provider(optional, provider_id="main")
    assert [item["method"] for item in transport.sent][-2:] == [
        "providers/list",
        "providers/disable",
    ]
