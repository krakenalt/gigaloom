"""Append-only, content-safe MCP probe history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gigaloom.sessions.contracts import exclusive_file_lock

from .contracts import MCPProbeResult
from .probe import mcp_probe_to_dict


class MCPProbeHistoryStore:
    """Append-only JSONL history for bounded MCP health results."""

    def __init__(self, data_dir: str | Path) -> None:
        self.path = Path(data_dir).expanduser() / "tools" / "probe_history.jsonl"

    def append(self, result: MCPProbeResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(mcp_probe_to_dict(result), sort_keys=True))
                handle.write("\n")

    def list(
        self, server_id: str | None = None, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with exclusive_file_lock(self.path):
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if server_id is None or item.get("server_id") == server_id:
                    rows.append(item)
        return rows[-max(1, min(limit, 100)) :][::-1]
