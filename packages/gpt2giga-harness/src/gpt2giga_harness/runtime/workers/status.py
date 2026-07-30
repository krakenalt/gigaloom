"""Worker lifecycle status projection."""

from __future__ import annotations

from datetime import datetime
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gpt2giga_harness.runtime.store import RuntimeCoordinationStore


def worker_status(
    store: RuntimeCoordinationStore,
    *,
    stale_after: float = 30.0,
) -> dict[str, Any]:
    """Return a JSON-ready worker status snapshot."""
    now = time.time()
    workers = []
    for worker in store.list_workers():
        try:
            heartbeat = datetime.fromisoformat(worker.heartbeat_at).timestamp()
        except ValueError:
            heartbeat = 0.0
        effective = (
            "offline"
            if worker.status == "online" and now - heartbeat > stale_after
            else worker.status
        )
        workers.append(
            {
                "id": worker.id,
                "process_id": worker.process_id,
                "hostname": worker.hostname,
                "status": effective,
                "started_at": worker.started_at,
                "heartbeat_at": worker.heartbeat_at,
                "stopped_at": worker.stopped_at,
                "capability_fingerprint": dict(worker.capability_fingerprint),
            }
        )
    return {
        "workers": workers,
        "online": sum(item["status"] == "online" for item in workers),
    }
