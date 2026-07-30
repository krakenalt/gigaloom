"""Read monotonic compatibility revisions for runtime consumers."""

from __future__ import annotations

from gigaloom.runtime.repositories.base import RuntimeRepository


class RevisionsRepository(RuntimeRepository):
    """Read durable monotonic revisions for runtime consumers."""

    def runs_center_revision(self) -> str:
        """Return the O(1) content-free Runs Center revision."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT revision FROM runtime_revisions
                WHERE topic = 'runs_center'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("runs_center runtime revision is unavailable")
        revision = int(row["revision"])
        if revision < 0:
            raise RuntimeError("runs_center runtime revision is invalid")
        return f"{revision:064x}"
