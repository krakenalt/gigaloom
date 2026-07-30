from dataclasses import dataclass
import json

import pytest

from gigaloom.contracts import (
    ProvenanceClass,
    Sensitivity,
    TrustClass,
)
from gigaloom.execution.api import (
    ExecutionTrustSnapshot,
    ExecutionTrustTracker,
    payload_digest,
)
from gigaloom.types import HarnessEvent


@dataclass(frozen=True)
class _Attachment:
    id: str
    sha256: str


def test_execution_trust_tracker_labels_real_ingress_and_output_lanes():
    tracker = ExecutionTrustTracker(
        user_prompt="review these inputs",
        attachments=(_Attachment("att_one", payload_digest(b"attachment bytes")),),
        managed_mcp_server_ids=("reviewed_server",),
    )

    tracker.observe_event(
        HarnessEvent(
            type="tool_call_finished",
            message="Web search completed.",
            payload={
                "name": "web_search",
                "result": {"title": "External result", "url": "https://example.test"},
            },
        )
    )
    tracker.observe_event(
        HarnessEvent(
            type="tool_call_finished",
            message="MCP tool completed.",
            payload={
                "name": "reviewed_server.lookup",
                "result": {"content": "MCP result"},
            },
        )
    )
    tracker.observe_event(
        HarnessEvent(
            type="stdout_delta",
            message="Terminal output.",
            payload={"delta": "Authorization: bearer abcdefgh"},
        )
    )
    tracker.observe_event(
        HarnessEvent(
            type="message_delta",
            message="Assistant output.",
            payload={"delta": "Generated answer"},
        )
    )

    snapshot = tracker.snapshot()
    parsed = ExecutionTrustSnapshot.from_dict(snapshot.to_dict())
    sources = {source.provenance: source for source in parsed.sources}

    assert sources[ProvenanceClass.USER].trust is TrustClass.TRUSTED
    assert sources[ProvenanceClass.ATTACHMENT].trust is TrustClass.BOUNDED
    assert sources[ProvenanceClass.WEB].trust is TrustClass.BOUNDED
    assert sources[ProvenanceClass.MCP].trust is TrustClass.BOUNDED
    assert sources[ProvenanceClass.TERMINAL].trust is TrustClass.UNTRUSTED
    assert sources[ProvenanceClass.TERMINAL].sensitivity is Sensitivity.SECRET
    assert sources[ProvenanceClass.GENERATED].trust is TrustClass.UNTRUSTED
    serialized = json.dumps(snapshot.to_dict(), sort_keys=True)
    assert "attachment bytes" not in serialized
    assert "External result" not in serialized
    assert "MCP result" not in serialized
    assert "abcdefgh" not in serialized
    assert "Generated answer" not in serialized


def test_execution_trust_tracker_conservatively_labels_dotted_tool_output_as_mcp():
    tracker = ExecutionTrustTracker(user_prompt="inspect")

    tracker.observe_event(
        HarnessEvent(
            type="tool_call_finished",
            message="Tool completed.",
            payload={"name": "server.tool", "result": "bounded result"},
        )
    )

    source = next(
        source
        for source in tracker.snapshot().sources
        if source.provenance is ProvenanceClass.MCP
    )
    assert source.trust is TrustClass.BOUNDED


def test_execution_trust_snapshot_rejects_digest_tampering():
    payload = ExecutionTrustTracker(user_prompt="inspect").snapshot().to_dict()
    payload["snapshot_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="digest does not match"):
        ExecutionTrustSnapshot.from_dict(payload)
