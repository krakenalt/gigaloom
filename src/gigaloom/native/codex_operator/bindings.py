"""Private durable native Codex session binding storage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

from gigaloom.native.codex_operator.session_contracts import (
    CODEX_SESSION_BINDING_SCHEMA_VERSION,
    CodexSessionBinding,
)
from gigaloom.native.terminal import TerminalAccess


MAX_CODEX_BINDING_BYTES = 64 * 1024
MAX_CODEX_BINDINGS = 1000


class CodexBindingAccessError(PermissionError):
    """Raised before a private binding crosses an authority boundary."""


class CodexBindingNotFoundError(KeyError):
    """Raised when a private native Codex binding does not exist."""


class CodexSessionBindingStore:
    """Persist strict private bindings without terminal output or credentials."""

    def __init__(self, state_root: str | Path) -> None:
        self.root = (
            Path(state_root).expanduser().resolve()
            / "native"
            / "codex"
            / "operator-bindings"
        )

    def save(self, binding: CodexSessionBinding) -> None:
        """Atomically save one strict mode-0600 private binding."""
        self._ensure_root()
        content = (
            json.dumps(
                _binding_to_storage(binding),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if len(content) > MAX_CODEX_BINDING_BYTES:
            raise ValueError("Codex binding is too large")
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{_binding_key(binding.id)}.",
            dir=self.root,
        )
        temporary = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path(binding.id))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def load(
        self,
        binding_id: str,
        access: TerminalAccess,
    ) -> CodexSessionBinding:
        """Load one exact binding and check authority before returning it."""
        path = self._path(binding_id)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_CODEX_BINDING_BYTES:
                raise ValueError("Codex binding file is invalid")
            payload = json.loads(path.read_bytes())
        except FileNotFoundError as exc:
            raise CodexBindingNotFoundError(binding_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Codex binding file is invalid") from exc
        binding = _binding_from_storage(payload)
        if binding.id != binding_id:
            raise ValueError("Codex binding identity changed")
        if binding.access != access:
            raise CodexBindingAccessError("Codex binding access does not match")
        return binding

    def count(self) -> int:
        """Return the bounded number of private binding records."""
        try:
            count = sum(1 for _ in self.root.glob("*.json"))
        except OSError as exc:
            raise ValueError("Codex binding registry is unreadable") from exc
        if count > MAX_CODEX_BINDINGS:
            raise ValueError("Codex binding registry is too large")
        return count

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        if stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise ValueError("Codex binding registry mode is invalid")
        if self.count() >= MAX_CODEX_BINDINGS:
            raise ValueError("Codex binding registry is full")

    def _path(self, binding_id: str) -> Path:
        return self.root / f"{_binding_key(binding_id)}.json"


def _binding_key(binding_id: str) -> str:
    return hashlib.sha256(binding_id.encode("utf-8")).hexdigest()


def _binding_to_storage(binding: CodexSessionBinding) -> dict[str, Any]:
    return {
        "schema_version": CODEX_SESSION_BINDING_SCHEMA_VERSION,
        "id": binding.id,
        "access": {
            "owner_id": binding.access.owner_id,
            "workspace_id": binding.access.workspace_id,
            "session_id": binding.access.session_id,
        },
        "terminal_id": binding.terminal_id,
        "terminal_revision": binding.terminal_revision,
        "cwd": binding.cwd,
        "cwd_digest": binding.cwd_digest,
        "web_url": binding.web_url,
        "generation": binding.generation,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
        "native_thread_id": binding.native_thread_id,
    }


def _binding_from_storage(value: Any) -> CodexSessionBinding:
    if not isinstance(value, Mapping):
        raise ValueError("Codex binding payload is invalid")
    expected = {
        "schema_version",
        "id",
        "access",
        "terminal_id",
        "terminal_revision",
        "cwd",
        "cwd_digest",
        "web_url",
        "generation",
        "created_at",
        "updated_at",
        "native_thread_id",
    }
    if set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("Codex binding schema is invalid")
    access = value.get("access")
    if not isinstance(access, Mapping) or set(access) != {
        "owner_id",
        "workspace_id",
        "session_id",
    }:
        raise ValueError("Codex binding access is invalid")
    return CodexSessionBinding(
        id=_required_text(value["id"], "id"),
        access=TerminalAccess(
            owner_id=_required_text(access["owner_id"], "owner id"),
            workspace_id=_required_text(access["workspace_id"], "workspace id"),
            session_id=_required_text(access["session_id"], "session id"),
        ),
        terminal_id=_required_text(value["terminal_id"], "terminal id"),
        terminal_revision=_positive_integer(
            value["terminal_revision"],
            "terminal revision",
        ),
        cwd=_required_text(value["cwd"], "cwd"),
        cwd_digest=_required_text(value["cwd_digest"], "cwd digest"),
        web_url=_required_text(value["web_url"], "Web URL"),
        generation=_positive_integer(value["generation"], "generation"),
        created_at=_required_text(value["created_at"], "created at"),
        updated_at=_required_text(value["updated_at"], "updated at"),
        native_thread_id=(
            _required_text(value["native_thread_id"], "native thread id")
            if value["native_thread_id"] is not None
            else None
        ),
    )


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Codex binding {field_name} is invalid")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Codex binding {field_name} is invalid")
    return value
