"""Store for the arena subcontext."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping
from gigaloom.automation.ports import redact_for_storage
from gigaloom.automation.ports import exclusive_file_lock
from gigaloom.automation.ports import new_id, utc_now
from .codec import (
    _mapping as _mapping,
    _redacted_mapping as _redacted_mapping,
    _write_json_atomic as _write_json_atomic,
    arena_from_dict as arena_from_dict,
    arena_to_dict as arena_to_dict,
)
from .constants import ARENA_REVIEW_SCHEMA_VERSION as ARENA_REVIEW_SCHEMA_VERSION
from .evidence import _arena_task_sha256 as _arena_task_sha256
from .models import (
    ArenaNotFoundError as ArenaNotFoundError,
    ArenaReviewConflictError as ArenaReviewConflictError,
    HarnessArenaChildRun as HarnessArenaChildRun,
    HarnessArenaRequest as HarnessArenaRequest,
    HarnessArenaRun as HarnessArenaRun,
)
from .status import _arena_status as _arena_status


class FilesystemHarnessArenaStore:
    """Persist arena parent records as transparent JSON files."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.arenas_dir = self.data_dir / "arenas"

    def create(
        self,
        request: HarnessArenaRequest,
        *,
        session_id: str,
    ) -> HarnessArenaRun:
        """Create one arena record."""
        now = utc_now()
        arena = HarnessArenaRun(
            id=new_id("arena"),
            session_id=session_id,
            status="running",
            prompt=str(redact_for_storage(request.prompt)),
            harness_ids=request.harness_ids,
            model=request.model,
            api_mode=request.api_mode,
            mode=request.mode,
            workspace=request.workspace,
            attachment_ids=request.attachment_ids,
            workspace_policy=request.workspace_policy,
            execution_transport=request.execution_transport,
            created_at=now,
            updated_at=now,
            metadata={
                **_redacted_mapping(request.extra),
                "reviewed_arena": {
                    "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
                    "task_sha256": _arena_task_sha256(request),
                },
            },
        )
        self.save(arena)
        return arena

    def get(self, arena_id: str) -> HarnessArenaRun:
        """Return one arena record."""
        path = self._path(arena_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArenaNotFoundError(arena_id) from exc
        return arena_from_dict(data)

    def list(
        self,
        *,
        workspace: str | None = None,
        limit: int | None = None,
    ) -> tuple[HarnessArenaRun, ...]:
        """List persisted arena records newest first."""
        arenas: list[HarnessArenaRun] = []
        if not self.arenas_dir.exists():
            return ()
        for path in self.arenas_dir.glob("*.json"):
            try:
                arena = arena_from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                continue
            if workspace is not None and arena.workspace != workspace:
                continue
            arenas.append(arena)
        arenas.sort(
            key=lambda arena: (arena.updated_at, arena.created_at), reverse=True
        )
        if limit is not None:
            arenas = arenas[: max(limit, 0)]
        return tuple(arenas)

    def save(self, arena: HarnessArenaRun) -> HarnessArenaRun:
        """Persist one arena record."""
        self.arenas_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(arena.id)
        with exclusive_file_lock(path):
            _write_json_atomic(path, arena_to_dict(arena))
        return arena

    def upsert_child(
        self, arena_id: str, child: HarnessArenaChildRun
    ) -> HarnessArenaRun:
        """Process-safely insert or replace one arena child by index."""
        path = self._path(arena_id)
        with exclusive_file_lock(path):
            arena = arena_from_dict(json.loads(path.read_text(encoding="utf-8")))
            children = [item for item in arena.child_runs if item.index != child.index]
            children.append(child)
            children.sort(key=lambda item: item.index)
            updated = replace(
                arena,
                child_runs=tuple(children),
                status=_arena_status(
                    tuple(children), expected_count=len(arena.harness_ids)
                ),
                updated_at=utc_now(),
            )
            _write_json_atomic(path, arena_to_dict(updated))
        return updated

    def record_verdict(
        self,
        arena_id: str,
        *,
        candidate_set_sha256: str,
        selected_child_index: int,
        selected_run_id: str,
        scores: tuple[Mapping[str, Any], ...],
        verdict_sha256: str,
    ) -> HarnessArenaRun:
        """Atomically persist one immutable operator verdict."""
        path = self._path(arena_id)
        with exclusive_file_lock(path):
            arena = arena_from_dict(json.loads(path.read_text(encoding="utf-8")))
            reviewed = dict(_mapping(arena.metadata.get("reviewed_arena")))
            existing = _mapping(reviewed.get("verdict"))
            if existing:
                if existing.get("verdict_sha256") != verdict_sha256:
                    raise ArenaReviewConflictError("arena verdict is already immutable")
                return arena
            reviewed["verdict"] = {
                "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
                "candidate_set_sha256": candidate_set_sha256,
                "selected_child_index": selected_child_index,
                "selected_run_id": selected_run_id,
                "scores": [dict(item) for item in scores],
                "decided_at": utc_now(),
                "verdict_sha256": verdict_sha256,
            }
            updated = replace(
                arena,
                metadata={**dict(arena.metadata), "reviewed_arena": reviewed},
                updated_at=utc_now(),
            )
            _write_json_atomic(path, arena_to_dict(updated))
        return updated

    def append_child(
        self,
        arena: HarnessArenaRun,
        child: HarnessArenaChildRun,
    ) -> HarnessArenaRun:
        """Append one child run and update arena status."""
        children = (*arena.child_runs, child)
        updated = replace(
            arena,
            child_runs=children,
            status=_arena_status(children, expected_count=len(arena.harness_ids)),
            updated_at=utc_now(),
        )
        return self.save(updated)

    def _path(self, arena_id: str) -> Path:
        return self.arenas_dir / f"{arena_id}.json"
