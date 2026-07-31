"""Managed MCP configuration application and headless snapshot services."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from gigaloom.sessions.contracts import exclusive_file_lock, utc_now
from gigaloom.secrets import SecretResolver
from gigaloom.tools import (
    CompositeSecretResolver,
    EnvironmentSecretResolver,
)

from .contracts import ToolServerDescriptor
from .managed_models import (
    HEADLESS_SNAPSHOT_MARKER,
    HeadlessManagedMCPSnapshot,
    ManagedConfigConflictError,
    ManagedConfigOwnershipError,
    ManagedConfigPlan,
    ManagedConfigResult,
)
from .managed_support import (
    MANAGED_MARKER,
    _atomic_write,
    _backup,
    _codex_block,
    _compose_resolved_managed_config,
    _content_hash,
    _descriptor_from_snapshot,
    _descriptor_to_snapshot,
    _json_hash,
    _json_servers,
    _ownership_path,
    _read_ownership,
    _read_text,
    _redacted_diff,
    _remove_codex_managed_block,
    _selected,
    _snapshot_from_record,
    _validate_harness,
    _validate_owned_home,
    _validate_project_id,
    _write_ownership,
)


SUPPORTED_HARNESSES = ("codex-cli", "claude-code", "gemini-cli")


class HeadlessManagedMCPSnapshotStore:
    """Persist immutable, redaction-safe headless MCP snapshots by content hash."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "tools" / "headless_mcp_snapshots"

    def create(
        self,
        *,
        project_id: str,
        harness_id: str,
        descriptors: Sequence[ToolServerDescriptor],
        server_ids: Sequence[str],
    ) -> HeadlessManagedMCPSnapshot:
        """Freeze exactly the requested trusted descriptors for one adapter."""
        _validate_harness(harness_id)
        _validate_project_id(project_id)
        requested = tuple(dict.fromkeys(str(item).strip() for item in server_ids))
        if not requested or any(not item for item in requested):
            raise ValueError("managed MCP server_ids must contain non-empty values")
        by_id = {item.id: item for item in descriptors}
        missing = sorted(set(requested) - set(by_id))
        if missing:
            raise ValueError(f"Managed MCP servers not found: {', '.join(missing)}")
        selected: list[ToolServerDescriptor] = []
        for server_id in requested:
            descriptor = by_id[server_id]
            if not descriptor.enabled:
                raise ValueError(f"Managed MCP server is disabled: {server_id}")
            if not descriptor.trusted:
                raise ValueError(f"Managed MCP server is not trusted: {server_id}")
            if descriptor.harnesses and harness_id not in descriptor.harnesses:
                raise ValueError(
                    f"Managed MCP server {server_id} is incompatible with {harness_id}"
                )
            selected.append(descriptor)
        content = {
            "schema_version": 1,
            "marker": HEADLESS_SNAPSHOT_MARKER,
            "project_id": project_id,
            "harness_id": harness_id,
            "server_ids": list(requested),
            "descriptors": [_descriptor_to_snapshot(item) for item in selected],
        }
        snapshot_hash = _json_hash(content)
        snapshot_id = f"mcp_{snapshot_hash[:32]}"
        path = self._path(snapshot_id)
        with exclusive_file_lock(self.root / f".{snapshot_id}"):
            if path.exists():
                return self.load(
                    {
                        "snapshot_id": snapshot_id,
                        "snapshot_hash": snapshot_hash,
                        "project_id": project_id,
                        "harness_id": harness_id,
                    }
                )
            record = {
                **content,
                "snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
                "created_at": utc_now(),
            }
            _atomic_write(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
        return _snapshot_from_record(record)

    def load(self, reference: Mapping[str, Any]) -> HeadlessManagedMCPSnapshot:
        """Load and integrity-check one stored snapshot reference."""
        snapshot_id = str(reference.get("snapshot_id") or "").strip()
        if not snapshot_id.startswith("mcp_") or not snapshot_id[4:].isalnum():
            raise ValueError("Invalid managed MCP snapshot id")
        try:
            record = json.loads(self._path(snapshot_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("Managed MCP snapshot was not found") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("Managed MCP snapshot is unreadable") from exc
        if not isinstance(record, Mapping):
            raise ValueError("Managed MCP snapshot must be an object")
        snapshot = _snapshot_from_record(record)
        expected_hash = str(reference.get("snapshot_hash") or "").strip()
        if (
            len(expected_hash) != 64
            or not expected_hash.isalnum()
            or expected_hash != snapshot.snapshot_hash
        ):
            raise ValueError("Managed MCP snapshot hash does not match")
        for field_name in ("project_id", "harness_id"):
            expected = str(reference.get(field_name) or "").strip()
            if expected and expected != str(getattr(snapshot, field_name)):
                raise ValueError(f"Managed MCP snapshot {field_name} does not match")
        return snapshot

    def _path(self, snapshot_id: str) -> Path:
        return self.root / f"{snapshot_id}.json"


class ManagedMCPConfigService:
    """Write MCP configuration only inside Harness-owned homes."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        home_active: Callable[[Path], bool] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.home_active = home_active or (lambda _home: False)

    def managed_home(self, harness_id: str, project_id: str) -> Path:
        """Return a validated Harness-owned native home."""
        _validate_harness(harness_id)
        if (
            not project_id.startswith("proj_")
            or not project_id.replace("_", "").isalnum()
        ):
            raise ValueError("Invalid project id")
        family = harness_id.removesuffix("-cli").replace("-code", "")
        home = self.data_dir / "native" / family / "homes" / project_id
        return _validate_owned_home(self.data_dir, home)

    def preview(
        self,
        harness_id: str,
        project_id: str,
        descriptors: Sequence[ToolServerDescriptor],
    ) -> ManagedConfigPlan:
        """Build an exact redacted diff without changing filesystem state."""
        home = self.managed_home(harness_id, project_id)
        path = config_path_for_home(harness_id, home)
        current = _read_text(path)
        composed, warnings = compose_managed_config(harness_id, current, descriptors)
        return ManagedConfigPlan(
            harness_id=harness_id,
            home=str(home),
            config_path=str(path),
            server_ids=tuple(item.id for item in _selected(descriptors, harness_id)),
            current_hash=_content_hash(current),
            content_hash=_content_hash(composed),
            changed=current != composed,
            diff=_redacted_diff(current, composed, path.name),
            warnings=warnings,
        )

    def apply(
        self,
        harness_id: str,
        project_id: str,
        descriptors: Sequence[ToolServerDescriptor],
        *,
        expected_hash: str,
    ) -> ManagedConfigResult:
        """Atomically apply a previously previewed managed configuration."""
        home = self.managed_home(harness_id, project_id)
        path = config_path_for_home(harness_id, home)
        lock_path = home / ".gpt2giga-config"
        home.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(lock_path):
            if self.home_active(home):
                raise ManagedConfigConflictError(
                    "Managed config cannot change while a native process owns this home"
                )
            current = _read_text(path)
            if _content_hash(current) != expected_hash:
                raise ManagedConfigConflictError(
                    "Managed config changed after preview; refresh the diff"
                )
            composed, _warnings = compose_managed_config(
                harness_id, current, descriptors
            )
            backup = _backup(path, current) if path.exists() else None
            _atomic_write(path, composed)
            selected = tuple(item.id for item in _selected(descriptors, harness_id))
            result = ManagedConfigResult(
                harness_id=harness_id,
                home=str(home),
                config_path=str(path),
                content_hash=_content_hash(composed),
                server_ids=selected,
                applied_at=utc_now(),
                backup_path=str(backup) if backup else None,
            )
            _write_ownership(home, result)
            return result

    def rollback(self, harness_id: str, project_id: str) -> ManagedConfigResult:
        """Restore the most recent backup after verifying ownership and hash."""
        home = self.managed_home(harness_id, project_id)
        path = config_path_for_home(harness_id, home)
        lock_path = home / ".gpt2giga-config"
        with exclusive_file_lock(lock_path):
            if self.home_active(home):
                raise ManagedConfigConflictError(
                    "Managed config cannot change while a native process owns this home"
                )
            marker = _read_ownership(home)
            if marker.get("marker") != MANAGED_MARKER:
                raise ManagedConfigOwnershipError("Managed ownership marker is missing")
            current = _read_text(path)
            if _content_hash(current) != marker.get("content_hash"):
                raise ManagedConfigOwnershipError(
                    "Managed config changed outside gpt2giga; refusing rollback"
                )
            backup_value = marker.get("backup_path")
            if backup_value:
                backup = _validate_owned_home(self.data_dir, Path(str(backup_value)))
                restored = backup.read_text(encoding="utf-8")
                _atomic_write(path, restored)
            else:
                path.unlink(missing_ok=True)
                restored = ""
            result = ManagedConfigResult(
                harness_id=harness_id,
                home=str(home),
                config_path=str(path),
                content_hash=_content_hash(restored),
                server_ids=(),
                applied_at=utc_now(),
                backup_path=str(backup_value) if backup_value else None,
                rolled_back=True,
            )
            _ownership_path(home).unlink(missing_ok=True)
            return result


def config_path_for_home(harness_id: str, home: Path) -> Path:
    """Return the CLI-specific config path inside one managed home."""
    _validate_harness(harness_id)
    if harness_id == "codex-cli":
        return home / "config.toml"
    if harness_id == "claude-code":
        return home / ".claude.json"
    return home / ".gemini" / "settings.json"


def compose_managed_config(
    harness_id: str,
    current: str,
    descriptors: Sequence[ToolServerDescriptor],
) -> tuple[str, tuple[str, ...]]:
    """Merge owned MCP entries while preserving non-MCP startup settings."""
    selected = _selected(descriptors, harness_id)
    if harness_id == "codex-cli":
        base = _remove_codex_managed_block(current).rstrip()
        block, warnings = _codex_block(selected)
        content = f"{base}\n\n{block}" if base else block
        return content.rstrip() + "\n", warnings
    data: dict[str, Any]
    try:
        parsed = json.loads(current) if current.strip() else {}
        data = dict(parsed) if isinstance(parsed, Mapping) else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Managed CLI JSON config is invalid") from exc
    entries, warnings = _json_servers(selected)
    data["mcpServers"] = entries
    data["_gpt2giga"] = {"marker": MANAGED_MARKER}
    return json.dumps(
        data, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n", warnings


def materialize_headless_mcp_snapshot(
    harness_id: str,
    home: str | Path,
    reference: Mapping[str, Any] | None,
    *,
    data_dir: str | Path | None,
    resolver: SecretResolver | None = None,
) -> Mapping[str, Any] | None:
    """Write one verified snapshot into the active temporary CLI home."""
    if reference is None:
        return None
    if data_dir is None:
        raise ValueError("Harness data_dir is required for managed MCP snapshots")
    snapshot = HeadlessManagedMCPSnapshotStore(data_dir).load(reference)
    if snapshot.harness_id != harness_id:
        raise ValueError("Managed MCP snapshot harness_id does not match adapter")
    home_path = Path(home).expanduser().resolve()
    path = config_path_for_home(harness_id, home_path)
    home_path.mkdir(parents=True, exist_ok=True)
    secret_resolver = resolver or CompositeSecretResolver(
        (EnvironmentSecretResolver(),)
    )
    descriptors = tuple(
        _descriptor_from_snapshot(item) for item in snapshot.descriptors
    )
    owner = f"headless-mcp:{snapshot.snapshot_id}:{harness_id}"
    with exclusive_file_lock(home_path / ".gpt2giga-config"):
        current = _read_text(path)
        content = _compose_resolved_managed_config(
            harness_id,
            current,
            descriptors,
            resolver=secret_resolver,
            owner=owner,
        )
        if content != current:
            _atomic_write(path, content)
    return {
        **snapshot.public_ref(),
        "materialized": True,
        "active_home": "temporary",
    }


def clear_headless_mcp_materialization(
    harness_id: str,
    home: str | Path,
) -> None:
    """Remove resolved MCP values after the owning process loaded its config."""
    home_path = Path(home).expanduser().resolve()
    path = config_path_for_home(harness_id, home_path)
    with exclusive_file_lock(home_path / ".gpt2giga-config"):
        current = _read_text(path)
        if harness_id == "codex-cli":
            content = _remove_codex_managed_block(current).rstrip() + "\n"
        else:
            try:
                parsed = json.loads(current) if current.strip() else {}
            except json.JSONDecodeError as exc:
                raise ValueError("Managed CLI JSON config is invalid") from exc
            data = dict(parsed) if isinstance(parsed, Mapping) else {}
            data.pop("mcpServers", None)
            data.pop("_gpt2giga", None)
            content = (
                json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            )
        if content != current:
            _atomic_write(path, content)


def compose_startup_config(
    harness_id: str,
    current: str,
    base: str | Mapping[str, Any],
) -> str:
    """Refresh CLI startup settings without erasing managed MCP entries."""
    _validate_harness(harness_id)
    if harness_id == "codex-cli":
        base_text = str(base).rstrip()
        begin = f"# BEGIN {MANAGED_MARKER}"
        block = ""
        if begin in current:
            block = begin + current.split(begin, 1)[1]
        return f"{base_text}\n\n{block}".rstrip() + "\n"
    try:
        parsed = json.loads(current) if current.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Managed CLI JSON config is invalid") from exc
    existing = dict(parsed) if isinstance(parsed, Mapping) else {}
    if not isinstance(base, Mapping):
        raise TypeError("JSON CLI startup settings must be a mapping")
    for key, value in base.items():
        if (
            harness_id == "claude-code"
            and key == "projects"
            and isinstance(existing.get(key), Mapping)
            and isinstance(value, Mapping)
        ):
            projects = dict(existing[key])
            for project_path, project_settings in value.items():
                current_settings = projects.get(project_path)
                if isinstance(current_settings, Mapping) and isinstance(
                    project_settings, Mapping
                ):
                    projects[project_path] = {
                        **dict(current_settings),
                        **dict(project_settings),
                    }
                else:
                    projects[project_path] = project_settings
            existing[key] = projects
        else:
            existing[key] = value
    return json.dumps(existing, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_startup_config(
    harness_id: str,
    home: str | Path,
    base: str | Mapping[str, Any],
) -> str:
    """Atomically refresh startup settings under the shared per-home lock."""
    home_path = Path(home).expanduser().resolve()
    path = config_path_for_home(harness_id, home_path)
    home_path.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(home_path / ".gpt2giga-config"):
        current = _read_text(path)
        content = compose_startup_config(harness_id, current, base)
        if content != current:
            _atomic_write(path, content)
            marker = dict(_read_ownership(home_path))
            if marker.get("marker") == MANAGED_MARKER and marker.get(
                "content_hash"
            ) == _content_hash(current):
                marker["content_hash"] = _content_hash(content)
                _atomic_write(
                    _ownership_path(home_path),
                    json.dumps(marker, indent=2, sort_keys=True) + "\n",
                )
        return _content_hash(content)


def managed_config_plan_to_dict(plan: ManagedConfigPlan) -> dict[str, Any]:
    """Serialize a managed config preview."""
    return asdict(plan)


def managed_config_result_to_dict(result: ManagedConfigResult) -> dict[str, Any]:
    """Serialize applied config provenance."""
    return asdict(result)
