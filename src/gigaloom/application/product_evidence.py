"""Application composition for local product evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any

from gigaloom.execution.thread_relay import (
    GIGALOOM_THREAD_ADAPTER_ID,
    GIGALOOM_THREAD_CAPABILITY_REVISION,
    LOCAL_THREAD_ACTOR_SCOPE,
)
from gigaloom.projects.api import FilesystemProjectCatalogRepository
from gigaloom.review.product_evidence import (
    ProductEvidenceSnapshotV1,
    build_product_evidence_report,
    collect_owner_product_facts,
)
from gigaloom.runtime.api import RuntimeCoordinationStore
from gigaloom.sessions.api import (
    ThreadDeliveryRecord,
    ThreadDeliveryRepository,
    ThreadLocatorV1,
    ThreadSourceKind,
)
from gigaloom.sessions.filesystem import FilesystemHarnessSessionStore


MAX_APPLICATION_DELIVERIES = 1_024


class ProductEvidenceApplication:
    """Compose read-only evidence over existing project/session/runtime owners."""

    def __init__(
        self,
        *,
        project_catalog: Any,
        session_owner: Any,
        runtime_owner: Any,
        delivery_owner: Any | None = None,
    ) -> None:
        self.project_catalog = project_catalog
        self.session_owner = session_owner
        self.runtime_owner = runtime_owner
        self.delivery_owner = delivery_owner

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> ProductEvidenceApplication:
        """Build from canonical local owners without creating optional state."""
        root = Path(data_dir).expanduser()
        relay_path = root / "sessions" / "thread_relay.sqlite3"
        return cls(
            project_catalog=FilesystemProjectCatalogRepository(
                root / "projects" / "catalog"
            ),
            session_owner=FilesystemHarnessSessionStore(root),
            runtime_owner=RuntimeCoordinationStore(root),
            delivery_owner=(
                ThreadDeliveryRepository(root) if relay_path.is_file() else None
            ),
        )

    def report(
        self,
        *,
        project_id: str,
        range_start: datetime,
        range_end: datetime,
        generated_at: datetime,
    ) -> Any:
        """Generate one deterministic report without persisting or uploading it."""
        project = self.project_catalog.get(project_id)
        snapshot = collect_owner_product_facts(
            project_id=project_id,
            project_created_at=project.created_at,
            range_start=range_start,
            range_end=range_end,
            session_owner=self.session_owner,
            runtime_owner=self.runtime_owner,
        )
        snapshot = self._with_relay_facts(
            snapshot,
            project_id=project_id,
            range_start=range_start,
            range_end=range_end,
        )
        return build_product_evidence_report(
            project_id=project_id,
            range_start=range_start,
            range_end=range_end,
            generated_at=generated_at,
            snapshot=snapshot,
        )

    def _with_relay_facts(
        self,
        snapshot: ProductEvidenceSnapshotV1,
        *,
        project_id: str,
        range_start: datetime,
        range_end: datetime,
    ) -> ProductEvidenceSnapshotV1:
        if self.delivery_owner is None:
            return snapshot
        records: dict[str, ThreadDeliveryRecord] = {}
        truncated = set(snapshot.truncated_sources)
        for session in snapshot.sessions:
            actor_scope = session.metadata.get("actor_scope")
            if actor_scope not in {None, LOCAL_THREAD_ACTOR_SCOPE}:
                continue
            locator = _local_locator(session, project_id)
            for direction in ("incoming", "outgoing"):
                remaining = MAX_APPLICATION_DELIVERIES - len(records)
                if remaining <= 0:
                    truncated.add("thread_relay")
                    break
                page = self.delivery_owner.list_for_thread(
                    locator,
                    direction=direction,
                    limit=min(50, remaining),
                )
                for record in page.items:
                    if _in_range(record.receipt.created_at, range_start, range_end):
                        records[record.receipt.delivery_id] = record
                if page.has_more:
                    truncated.add("thread_relay")
                if len(records) >= MAX_APPLICATION_DELIVERIES:
                    truncated.add("thread_relay")
                    break
            if len(records) >= MAX_APPLICATION_DELIVERIES:
                break
        return replace(
            snapshot,
            deliveries=tuple(
                sorted(records.values(), key=lambda item: item.receipt.delivery_id)
            ),
            available_sources=(*snapshot.available_sources, "thread_relay"),
            truncated_sources=tuple(sorted(truncated)),
        )


def _local_locator(session: Any, project_id: str) -> ThreadLocatorV1:
    workspace_identity = _optional_identity(session.metadata.get("workspace_identity"))
    if workspace_identity is None and session.workspace:
        workspace_identity = (
            "sha256:" + hashlib.sha256(session.workspace.encode("utf-8")).hexdigest()
        )
    return ThreadLocatorV1(
        source_kind=ThreadSourceKind.GIGALOOM,
        adapter_id=GIGALOOM_THREAD_ADAPTER_ID,
        project_id=project_id,
        thread_id=session.id,
        actor_scope=LOCAL_THREAD_ACTOR_SCOPE,
        workspace_identity=workspace_identity,
        provider_session_ref=None,
        capability_revision=GIGALOOM_THREAD_CAPABILITY_REVISION,
    )


def _optional_identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > 256
        or not all(character.isalnum() or character in "._:/@+~-" for character in text)
    ):
        return None
    return text


def _in_range(value: datetime, start: datetime, end: datetime) -> bool:
    return start <= value <= end


__all__ = ["ProductEvidenceApplication"]
