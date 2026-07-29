"""Review service primitives."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from gpt2giga_harness.review.ports import (
    EnvironmentCaptureError,
    GitEnvironmentProvider,
)
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.review.ports import ApprovalStatus
from gpt2giga_harness.review.ports import RuntimeCoordinationStore
from gpt2giga_harness.review.ports import HarnessRun
from gpt2giga_harness.review.ports import HarnessSessionStore
from gpt2giga_harness.types import spec_to_dict
from .codec import _json_hash, _optional_identity, _required_identity, _semantic_hash
from .models import (
    EnvironmentSnapshotProvider,
    HANDOFF_CAPSULE_SCHEMA_VERSION,
    HandoffCapsuleError,
    MAX_CAPSULE_ARTIFACTS,
)
from .projections import (
    _artifact_projection,
    _diff_projection,
    _provider_projection,
    _tool_extension_projection,
    _unresolved_questions,
)
from .validation import verify_handoff_capsule


class HandoffCapsuleService:
    """Build one immutable handoff view from existing run authorities."""

    def __init__(
        self,
        *,
        store: HarnessSessionStore,
        registry: HarnessRegistry,
        runtime_store: RuntimeCoordinationStore | None = None,
        environment_provider: EnvironmentSnapshotProvider | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.runtime_store = runtime_store
        self.environment_provider = environment_provider

    def build(self, run_id: str, target_harness_id: str) -> dict[str, Any]:
        """Build a deterministic capsule without starting or mutating a Harness."""
        run = self.store.get_run(_required_identity(run_id, "run_id"))
        target_id = _required_identity(target_harness_id, "target_harness_id")
        if target_id == run.harness_id:
            raise HandoffCapsuleError(
                "cross-Harness handoff requires a different target harness"
            )
        source_harness = self.registry.get(run.harness_id)
        target_harness = self.registry.get(target_id)
        environment = self._environment(run)
        events = self.store.list_events(run.session_id, run_id=run.id)
        approvals = self._pending_approvals(run)
        questions = _unresolved_questions(events)
        artifacts = _artifact_projection(run, events, environment)
        tool_snapshot = _tool_extension_projection(run)
        source_spec_hash = _semantic_hash(
            "source-harness-contract", spec_to_dict(source_harness.spec())
        )
        target_spec_hash = _semantic_hash(
            "target-harness-contract", spec_to_dict(target_harness.spec())
        )
        body = {
            "schema_version": HANDOFF_CAPSULE_SCHEMA_VERSION,
            "kind": "agent_workbench.handoff_capsule.v1",
            "content_free": True,
            "summary": {
                "source_status": run.status.value,
                "mode": run.mode,
                "capability": run.capability.value,
                "invocation_mode": run.invocation_mode.value,
                "task_sha256": _semantic_hash("task", run.prompt),
                "artifact_count": len(artifacts),
                "pending_approval_count": len(approvals),
                "unresolved_question_count": len(questions),
            },
            "diff_and_artifacts": {
                "diff": _diff_projection(run),
                "artifacts": artifacts,
            },
            "tool_extension_snapshot": tool_snapshot,
            "provenance": {
                "source": {
                    "run_id": run.id,
                    "session_id": run.session_id,
                    "harness_id": run.harness_id,
                    "harness_contract_sha256": source_spec_hash,
                    "provider": _provider_projection(run),
                },
                "target": {
                    "harness_id": target_id,
                    "harness_contract_sha256": target_spec_hash,
                    "session_requirement": "new_or_explicit_import",
                },
            },
            "unresolved": {
                "approvals": approvals,
                "questions": questions,
                "approval_source": (
                    "runtime_store" if self.runtime_store is not None else "unavailable"
                ),
            },
            "environment": environment,
            "continuity": {
                "native_session_identity_preserved": False,
                "provider_session_identity_preserved": False,
                "harness_session_identity_preserved": False,
                "source_native_session_present": bool(run.native_session_id),
                "claim": "evidence_handoff_only",
            },
        }
        capsule_sha256 = _json_hash(body)
        return verify_handoff_capsule(
            {
                **body,
                "capsule_id": f"handoff_{capsule_sha256[:32]}",
                "capsule_sha256": capsule_sha256,
            }
        )

    def _environment(self, run: HarnessRun) -> dict[str, Any]:
        if run.workspace is None:
            raise HandoffCapsuleError(
                "handoff capsule requires a workspace-bound source run"
            )
        try:
            provider = self.environment_provider or GitEnvironmentProvider()
            snapshot = provider.snapshot(run.workspace)
        except EnvironmentCaptureError as exc:
            raise HandoffCapsuleError(
                f"environment capture failed: {exc.code}"
            ) from exc
        semantic = {
            key: value
            for key, value in snapshot.to_dict().items()
            if key not in {"captured_at", "repository_root", "worktree_root"}
        }
        return {
            "provider_id": snapshot.provider_id,
            "workspace_sha256": _semantic_hash(
                "workspace", str(Path(run.workspace).expanduser().resolve())
            ),
            "branch": snapshot.branch,
            "detached": snapshot.detached,
            "head": snapshot.head,
            "base_identity": snapshot.base_identity,
            "upstream": snapshot.upstream,
            "ahead": snapshot.ahead,
            "behind": snapshot.behind,
            "diff_sha256": snapshot.diff_sha256,
            "changed_paths": list(snapshot.changed_paths),
            "changed_paths_truncated": snapshot.changed_paths_truncated,
            "snapshot_sha256": _semantic_hash("environment", semantic),
        }

    def _pending_approvals(self, run: HarnessRun) -> list[dict[str, Any]]:
        if self.runtime_store is None:
            return []
        job_id = _optional_identity(run.metadata.get("job_id"))
        requests = self.runtime_store.list_run_approval_requests(
            run_id=run.id,
            job_id=job_id,
            limit=MAX_CAPSULE_ARTIFACTS,
        )
        return [
            {
                "id": item.id,
                "action": item.action.value,
                "enforcement": item.enforcement.value,
                "enforcement_owner": item.enforcement_owner,
                "created_at": item.created_at,
                "expires_at": item.expires_at,
            }
            for item in requests
            if item.status is ApprovalStatus.PENDING
        ]
