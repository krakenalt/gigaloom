"""Frozen ACP SDK and schema evidence contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).parents[3]
EVIDENCE = (
    ROOT
    / "src"
    / "gigaloom"
    / "harnesses"
    / "acp"
    / "evidence"
    / "python-sdk-0.11.1.json"
)


def test_acp_sdk_dependency_and_evidence_are_exact() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-client-protocol==0.11.1" in metadata["project"]["dependencies"]

    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["package"] == {
        "name": "agent-client-protocol",
        "version": "0.11.1",
        "python_requires": ">=3.10,<3.15",
        "repository": "https://github.com/agentclientprotocol/python-sdk",
        "tag": "0.11.1",
        "commit": "02b6f874525927a501daf8004cae8a30f91723a2",
    }
    assert evidence["protocol"]["wire_version"] == 1
    assert evidence["protocol"]["generated_schema_ref"] == ("refs/tags/schema-v1.16.0")
    assert evidence["reviewed_contract"] == {
        "transport": "local_stdio_json_lines",
        "wire_compatibility_source": "initialize.protocolVersion",
        "optional_feature_source": "initialize.agentCapabilities",
        "unstable_protocol_enabled": False,
        "remote_transport_admitted": False,
    }
