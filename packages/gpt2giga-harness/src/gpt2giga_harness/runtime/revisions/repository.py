"""Read compatibility revisions for runtime consumers."""

from __future__ import annotations

import hashlib
import json

from gpt2giga_harness.runtime.repositories.base import RuntimeRepository


class RevisionsRepository(RuntimeRepository):
    """Read compatibility revisions for runtime consumers."""

    def runs_center_revision(self) -> str:
        """Return a content-free revision excluding lease heartbeat churn."""
        with self._connect() as connection:
            jobs = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(version), 0) FROM jobs"
            ).fetchone()
            approvals = connection.execute(
                """
                SELECT status, COUNT(*), COALESCE(MAX(COALESCE(decided_at, created_at)), '')
                FROM approval_requests GROUP BY status ORDER BY status
                """
            ).fetchall()
            workers = connection.execute(
                """
                SELECT status, COUNT(*), COALESCE(MAX(COALESCE(stopped_at, started_at)), '')
                FROM workers GROUP BY status ORDER BY status
                """
            ).fetchall()
        payload = {
            "approvals": [tuple(row) for row in approvals],
            "jobs": tuple(jobs),
            "workers": [tuple(row) for row in workers],
        }
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
