"""Persistence for Codex app-server session links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gigaloom.harnesses.ports import exclusive_file_lock
from gigaloom.types import (
    redact_secrets,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    AppServerProtocolError,
)
from gigaloom.harnesses.builtins.codex.app_server.utils import (
    _atomic_write,
    _safe_id,
)


class CodexAppServerLinkStore:
    """Persist redaction-safe thread links and prompt delivery state atomically."""

    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "app_server" / "links"

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Load one link, returning ``None`` when it has not been created."""
        path = self._path(session_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            raise AppServerProtocolError("Codex app-server link is unreadable") from exc
        if not isinstance(value, Mapping):
            raise AppServerProtocolError("Codex app-server link must be an object")
        return dict(value)

    def save(self, session_id: str, link: Mapping[str, Any]) -> dict[str, Any]:
        """Write one public link without prompt text, stdio, credentials, or PIDs."""
        payload = dict(redact_secrets(dict(link)))
        path = self._path(session_id)
        with exclusive_file_lock(self.root / f".{_safe_id(session_id)}"):
            _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload

    def _path(self, session_id: str) -> Path:
        return self.root / f"{_safe_id(session_id)}.json"
