"""Shared credential-free route facts for native and managed ACP consumers."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import json
from pathlib import Path
from typing import Any

import pytest

from gigaloom.native.api import (
    GatewayRouteRefusal,
    GatewayRouteResolver,
    ResolvedGatewayRoute,
    gateway_artifact_admitted,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "acp"
    / "provider_bridge"
    / "openai_chat_completions_route.json"
)


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _route(**changes: object) -> ResolvedGatewayRoute:
    values: dict[str, Any] = {
        "route_id": "acp-gpt2giga-gigachat-2-max",
        "gateway_id": "gpt2giga",
        "provider_protocol": "openai_chat_completions",
        "credential_free_base_url": "http://127.0.0.1:8090/v1",
        "public_model_alias": "GigaChat-2-Max",
        "support_status": "stable",
        "capability_digest": "f" * 64,
        "reason_ids": ("baseline_gigachat_chat_conformance",),
    }
    values.update(changes)
    return ResolvedGatewayRoute(**values)


def test_managed_acp_accepts_a_route_with_an_openai_provider_protocol() -> None:
    fixture = _fixture()
    values = dict(fixture["discovered_route"])
    values["reason_ids"] = tuple(values["reason_ids"])

    route = ResolvedGatewayRoute(**values)

    assert fixture["requested_agent_kind"] == "managed_acp"
    assert fixture["transport_kind"] == "acp_stdio_v1"
    assert route.provider_protocol == "openai_chat_completions"
    assert route.provider_protocol != fixture["transport_kind"]
    assert route.support_status == "stable"
    assert fixture["expected_resolution"]["accepted"] is True


def test_resolved_route_is_frozen_slotted_and_has_no_secret_field() -> None:
    route = _route()
    field_names = {field.name for field in fields(route)}

    assert "api_key" not in field_names
    assert "authorization" not in field_names
    assert not hasattr(route, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(route, "gateway_id", "other")


def test_gateway_resolution_and_artifact_admission_are_public_native_contracts() -> (
    None
):
    assert GatewayRouteResolver.__name__ == "GatewayRouteResolver"
    assert GatewayRouteRefusal.__name__ == "GatewayRouteRefusal"
    assert callable(gateway_artifact_admitted)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"credential_free_base_url": "http://secret@127.0.0.1:8090/v1"},
            "credential-free",
        ),
        ({"support_status": "unknown"}, "support status"),
        ({"capability_digest": "sha256:not-a-digest"}, "capability digest"),
        ({"reason_ids": ("duplicate", "duplicate")}, "reason ids"),
    ],
)
def test_resolved_route_rejects_ambiguous_or_secret_bearing_facts(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _route(**changes)
