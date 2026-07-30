"""Typed continuation decision for one Harness request."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import hashlib
from typing import Any

from gpt2giga_harness.codex_app_server import build_execution_snapshot
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.types import (
    HarnessRequest,
    HeadlessContinuationStrategy,
)


@dataclass(frozen=True)
class ContinuationPlan(Mapping[str, Any]):
    """Machine-readable continuation strategy with explicit serialization."""

    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.payload)

    def __len__(self) -> int:
        return len(self.payload)

    def to_dict(self) -> dict[str, Any]:
        """Return the delivery payload without exposing mutable internal state."""
        return dict(self.payload)

    def public_payload(self) -> dict[str, Any]:
        """Remove delivery-only identifiers from persisted public evidence."""
        return {
            key: item
            for key, item in self.payload.items()
            if key not in {"prompt_id", "link"}
        }


def build_continuation_plan(
    request: HarnessRequest,
    *,
    harness: Any,
    session: Any,
    previous_messages: tuple[Any, ...],
    prompt_id: str,
    edit_source: Mapping[str, Any] | None = None,
) -> ContinuationPlan:
    """Select one truthful, machine-readable headless continuation strategy."""
    if (
        request.execution_transport is ExecutionTransport.NATIVE_STRUCTURED
        and harness.spec().id != "codex-cli"
    ):
        return ContinuationPlan(
            {
                "strategy": ExecutionTransport.NATIVE_STRUCTURED.value,
                "supported": True,
                "continuity_proven": True,
                "action": (
                    "start"
                    if edit_source is not None
                    else "continue"
                    if previous_messages
                    else "start"
                ),
                "prompt_id": prompt_id,
                "history_replayed": edit_source is not None and bool(previous_messages),
            }
        )
    if (
        request.invocation_mode.value != "headless"
        and request.execution_transport is not ExecutionTransport.NATIVE_STRUCTURED
    ):
        return ContinuationPlan(
            {
                "strategy": HeadlessContinuationStrategy.NATIVE_CLI_RESUME.value,
                "supported": bool(request.native_session_id),
                "reason": (
                    "Native continuity is owned by the managed native connector."
                ),
            }
        )
    spec = harness.spec()
    configured = getattr(
        spec,
        "headless_continuation",
        HeadlessContinuationStrategy.ONE_SHOT,
    )
    strategy = (
        configured.value
        if isinstance(configured, HeadlessContinuationStrategy)
        else str(configured)
    )
    if strategy == HeadlessContinuationStrategy.STRUCTURED_THREAD.value:
        probe = getattr(harness, "capability_probe", None)
        snapshot = probe() if callable(probe) else None
        capabilities = getattr(snapshot, "capabilities", {})
        if not isinstance(capabilities, Mapping) or not capabilities.get("app-server"):
            return ContinuationPlan(
                {
                    "strategy": HeadlessContinuationStrategy.DEGRADED_REPLAY.value,
                    "supported": True,
                    "continuity_proven": False,
                    "reason": (
                        "Codex app-server is unavailable; normalized history is "
                        "replayed into a fresh codex exec --ephemeral process."
                    ),
                }
            )
        managed_mcp = _mapping(request.extra.get("managed_mcp_snapshot"))
        home_identity = (
            "apphome_"
            + hashlib.sha256(
                (
                    f"{request.api_mode.value}\0"
                    f"{managed_mcp.get('snapshot_hash') or 'no-tools'}"
                ).encode("utf-8")
            ).hexdigest()[:24]
        )
        execution_snapshot = build_execution_snapshot(
            request,
            managed_home_id=home_identity,
        )
        link = _mapping(session.metadata.get("app_server_thread"))
        fork = _mapping(session.metadata.get("app_server_fork"))
        native_operation = str(
            request.extra.get("native_session_operation") or ""
        ).strip()
        if request.native_session_id and not link and not fork:
            if native_operation == "resume":
                link = {
                    "schema_version": 1,
                    "protocol": "codex-app-server-json-rpc-v2",
                    "thread_id": request.native_session_id,
                    "snapshot": execution_snapshot,
                    "snapshot_hash": execution_snapshot["snapshot_hash"],
                    "runtime_status": "external",
                }
            elif native_operation == "fork":
                fork = {"thread_id": request.native_session_id}
            else:
                raise ValueError(
                    "Codex native session identity requires resume or fork"
                )
        if edit_source is not None:
            link = _mapping(edit_source.get("link"))
            fork = (
                {
                    "thread_id": edit_source.get("thread_id"),
                    "turn_id": edit_source.get("turn_id"),
                }
                if edit_source.get("action") == "fork"
                else {}
            )
        if link:
            expected = str(link.get("snapshot_hash") or "")
            if expected != execution_snapshot["snapshot_hash"]:
                raise ValueError(
                    "Codex app-server continuation changed route, model, workspace, "
                    "permission mode, managed home, or tool snapshot; fork explicitly."
                )
        action = (
            "fork"
            if fork
            else "resume"
            if native_operation == "resume" and link
            else "continue"
            if link
            else "start"
        )
        return ContinuationPlan(
            {
                "strategy": HeadlessContinuationStrategy.STRUCTURED_THREAD.value,
                "supported": True,
                "continuity_proven": True,
                "action": action,
                "prompt_id": prompt_id,
                "snapshot": execution_snapshot,
                "link": link or None,
                "fork_thread_id": fork.get("thread_id"),
                "fork_turn_id": fork.get("turn_id"),
                "protocol": "codex-app-server-json-rpc-v2",
                "cli_version": str(getattr(snapshot, "version", None) or "unknown"),
                "normalized_history_canonical": True,
                "history_replayed": False,
            }
        )
    if strategy == HeadlessContinuationStrategy.STRUCTURED_REPLAY.value:
        return ContinuationPlan(
            {
                "strategy": strategy,
                "supported": True,
                "continuity_proven": True,
                "history_replayed": bool(previous_messages),
                "reason": (
                    "Normalized Harness messages are sent as one structured request."
                ),
            }
        )
    if strategy == HeadlessContinuationStrategy.UNSUPPORTED.value:
        return ContinuationPlan(
            {
                "strategy": strategy,
                "supported": False,
                "continuity_proven": False,
                "reason": (
                    f"{spec.title} headless mode does not consume a stable external "
                    "session id or normalized prior turns; this run is one-shot."
                ),
            }
        )
    return ContinuationPlan(
        {
            "strategy": HeadlessContinuationStrategy.ONE_SHOT.value,
            "supported": False,
            "continuity_proven": False,
            "reason": "This adapter advertises one-shot execution only.",
        }
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
