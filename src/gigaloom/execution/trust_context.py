"""Content-free trust labels propagated through one harness execution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Protocol

from gigaloom.contracts import (
    MAX_INFLUENCE_SOURCES,
    ProvenanceClass,
    Sensitivity,
    SourceRef,
    source_ref_from_dict,
    source_ref_to_dict,
)
from gigaloom.execution.trust_adapters import (
    attachment_source_ref,
    generated_source_ref,
    mcp_source_ref,
    terminal_source_ref,
    user_source_ref,
    web_source_ref,
)


EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION = 1
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_DYNAMIC_SOURCE_IDS = {
    ProvenanceClass.WEB: "web:run-results",
    ProvenanceClass.MCP: "mcp:run-results",
    ProvenanceClass.TERMINAL: "terminal:run-output",
    ProvenanceClass.GENERATED: "generated:run-output",
}
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.SECRET: 2,
}


class AttachmentSource(Protocol):
    """Attachment fields consumed without importing the attachment owner."""

    id: str
    sha256: str


class HarnessEventSource(Protocol):
    """Normalized event fields consumed without importing a provider adapter."""

    type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionTrustSnapshot:
    """Digest-bound trust projection for one point in a run."""

    sources: tuple[SourceRef, ...]
    schema_version: int = EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported execution trust context schema_version")
        ordered = tuple(sorted(self.sources, key=lambda source: source.source_id))
        if not ordered or len(ordered) > MAX_INFLUENCE_SOURCES:
            raise ValueError("execution trust context sources are invalid")
        if len({source.source_id for source in ordered}) != len(ordered):
            raise ValueError("execution trust context source ids must be unique")
        object.__setattr__(self, "sources", ordered)

    @property
    def snapshot_sha256(self) -> str:
        """Return the canonical digest of the content-free source projection."""
        return _json_digest(self._body())

    def to_dict(self) -> dict[str, Any]:
        """Serialize labels without source content."""
        return {**self._body(), "snapshot_sha256": self.snapshot_sha256}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionTrustSnapshot:
        """Parse and verify one strict execution trust snapshot."""
        expected = {"schema_version", "sources", "snapshot_sha256"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("execution trust context fields are invalid")
        raw_sources = payload["sources"]
        if not isinstance(raw_sources, list):
            raise ValueError("execution trust context sources must be a list")
        snapshot = cls(
            schema_version=_required_int(payload["schema_version"]),
            sources=tuple(
                source_ref_from_dict(_required_mapping(item)) for item in raw_sources
            ),
        )
        if payload["snapshot_sha256"] != snapshot.snapshot_sha256:
            raise ValueError("execution trust context digest does not match")
        return snapshot

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sources": [source_ref_to_dict(source) for source in self.sources],
        }


class ExecutionTrustTracker:
    """Aggregate real ingress and normalized output without retaining content."""

    def __init__(
        self,
        *,
        user_prompt: str | bytes,
        attachments: Iterable[AttachmentSource] = (),
        managed_mcp_server_ids: Iterable[str] = (),
    ) -> None:
        user = user_source_ref("user:current-prompt", user_prompt)
        sources = {user.source_id: user}
        for attachment in attachments:
            source = attachment_source_ref(
                _source_id("attachment", attachment.id),
                attachment.sha256,
            )
            if source.source_id in sources:
                raise ValueError("execution trust context has duplicate attachments")
            sources[source.source_id] = source
        if len(sources) > MAX_INFLUENCE_SOURCES - len(_DYNAMIC_SOURCE_IDS):
            raise ValueError(
                "execution trust context leaves no bounded output capacity"
            )
        self._sources = sources
        self._managed_mcp_server_ids = frozenset(
            _required_identity(item, field_name="managed MCP server id")
            for item in managed_mcp_server_ids
        )
        self._dynamic_counts: dict[ProvenanceClass, int] = {}

    @classmethod
    def from_execution_options(
        cls,
        options: Mapping[str, Any],
        attachments: Iterable[AttachmentSource] = (),
    ) -> ExecutionTrustTracker:
        """Build ingress labels from the admitted runner option shape."""
        extra = options.get("extra")
        tool_ids = extra.get("tool_ids") if isinstance(extra, Mapping) else ()
        if tool_ids is None:
            tool_ids = ()
        if not isinstance(tool_ids, (list, tuple)):
            raise ValueError("tool_ids must be a list")
        return cls(
            user_prompt=str(options.get("prompt") or ""),
            attachments=attachments,
            managed_mcp_server_ids=tool_ids,
        )

    def observe_event(self, event: HarnessEventSource) -> None:
        """Classify one normalized event using conservative provider-neutral rules."""
        event_type = str(event.type)
        payload = dict(event.payload)
        if event_type == "tool_call_finished":
            result = payload.get("result")
            if result in (None, "", (), [], {}):
                return
            name = str(payload.get("name") or "")
            explicit = str(payload.get("source_provenance") or "").lower()
            if name == "web_search" or explicit == ProvenanceClass.WEB.value:
                self._observe(ProvenanceClass.WEB, result)
            elif explicit == ProvenanceClass.MCP.value or _looks_like_mcp_tool(
                name, self._managed_mcp_server_ids
            ):
                self._observe(ProvenanceClass.MCP, result)
            return
        if event_type in {"stdout_delta", "stderr_delta"}:
            self._observe(ProvenanceClass.TERMINAL, payload.get("delta"))
            return
        if event_type in {"message_delta", "message_completed"}:
            self._observe(
                ProvenanceClass.GENERATED,
                payload.get("delta") or payload.get("content"),
            )

    def observe_generated_output(self, content: str | bytes) -> None:
        """Retain final model output influence when no message event carried it."""
        if self._dynamic_counts.get(ProvenanceClass.GENERATED, 0) == 0:
            self._observe(ProvenanceClass.GENERATED, content)

    def snapshot(self) -> ExecutionTrustSnapshot:
        """Return the current immutable content-free projection."""
        return ExecutionTrustSnapshot(tuple(self._sources.values()))

    def _observe(self, provenance: ProvenanceClass, content: Any) -> None:
        if content in (None, "", (), [], {}):
            return
        encoded = _canonical_content(content)
        observed = _source_for_content(provenance, encoded)
        source_id = _DYNAMIC_SOURCE_IDS[provenance]
        previous = self._sources.get(source_id)
        digest = _aggregate_digest(
            previous.content_sha256 if previous is not None else None,
            encoded,
        )
        sensitivity = (
            observed.sensitivity
            if previous is None
            else _max_sensitivity(previous.sensitivity, observed.sensitivity)
        )
        self._sources[source_id] = SourceRef(
            source_id=source_id,
            provenance=observed.provenance,
            trust=observed.trust,
            sensitivity=sensitivity,
            content_sha256=digest,
        )
        self._dynamic_counts[provenance] = self._dynamic_counts.get(provenance, 0) + 1


def _source_for_content(provenance: ProvenanceClass, content: bytes) -> SourceRef:
    factory = {
        ProvenanceClass.WEB: web_source_ref,
        ProvenanceClass.MCP: mcp_source_ref,
        ProvenanceClass.TERMINAL: terminal_source_ref,
        ProvenanceClass.GENERATED: generated_source_ref,
    }[provenance]
    return factory(_DYNAMIC_SOURCE_IDS[provenance], content)


def _looks_like_mcp_tool(name: str, server_ids: frozenset[str]) -> bool:
    if not name or name == "web_search":
        return False
    if any(name.startswith(f"{server_id}.") for server_id in server_ids):
        return True
    # Codex app-server normalizes MCP tool names as ``server.tool``. Treating
    # an unknown dotted tool as bounded MCP influence is conservative: it can
    # only remove authority, never create it.
    return "." in name and not name.startswith(("shell.", "git."))


def _canonical_content(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("execution source content is not canonical") from exc


def _aggregate_digest(previous: str | None, content: bytes) -> str:
    digest = hashlib.sha256()
    if previous is not None:
        digest.update(bytes.fromhex(previous))
    digest.update(len(content).to_bytes(8, byteorder="big"))
    digest.update(content)
    return digest.hexdigest()


def _source_id(prefix: str, value: Any) -> str:
    text = str(value)
    candidate = f"{prefix}:{text}"
    if _IDENTITY_RE.fullmatch(candidate):
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _required_identity(value: Any, *, field_name: str) -> str:
    text = str(value)
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _max_sensitivity(left: Sensitivity, right: Sensitivity) -> Sensitivity:
    return left if _SENSITIVITY_RANK[left] >= _SENSITIVITY_RANK[right] else right


def _json_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("execution trust source must be an object")
    return value


def _required_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("execution trust schema_version must be an integer")
    return value


__all__ = [
    "EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION",
    "ExecutionTrustSnapshot",
    "ExecutionTrustTracker",
]
