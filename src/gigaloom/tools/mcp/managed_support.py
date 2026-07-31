"""Private composition, persistence, and snapshot helpers for managed MCP."""

from __future__ import annotations

from dataclasses import asdict
import difflib
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from gigaloom.secrets import (
    SecretReference,
    SecretResolver,
    secret_reference_from_dict,
    secret_reference_to_dict,
)

from .contracts import MCPTransport, ToolServerDescriptor
from .managed_models import (
    HEADLESS_SNAPSHOT_MARKER,
    HeadlessManagedMCPSnapshot,
    ManagedConfigOwnershipError,
    ManagedConfigResult,
)


MANAGED_MARKER = "gpt2giga-managed-mcp-v1"
SUPPORTED_HARNESSES = ("codex-cli", "claude-code", "gemini-cli")


def _selected(
    descriptors: Sequence[ToolServerDescriptor], harness_id: str
) -> tuple[ToolServerDescriptor, ...]:
    _validate_harness(harness_id)
    return tuple(
        sorted(
            (
                item
                for item in descriptors
                if item.enabled
                and item.trusted
                and (not item.harnesses or harness_id in item.harnesses)
            ),
            key=lambda item: item.id,
        )
    )


def _codex_block(
    descriptors: Sequence[ToolServerDescriptor],
    *,
    resolver: SecretResolver | None = None,
    owner: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    lines = [f"# BEGIN {MANAGED_MARKER}"]
    warnings: list[str] = []
    for item in descriptors:
        lines.append(f"[mcp_servers.{_toml_key(item.id)}]")
        if item.transport is MCPTransport.STDIO:
            lines.append(f'command = "{_toml_string(item.command or "")}"')
            if item.args:
                values = ", ".join(f'"{_toml_string(value)}"' for value in item.args)
                lines.append(f"args = [{values}]")
            environment, skipped = _literal_values(
                item.environment, resolver=resolver, owner=owner
            )
            warnings.extend(f"{item.id}: {value}" for value in skipped)
            if environment:
                lines.append(f"[mcp_servers.{_toml_key(item.id)}.env]")
                for key, value in sorted(environment.items()):
                    lines.append(f'{_toml_key(key)} = "{_toml_string(value)}"')
        else:
            lines.append(f'url = "{_toml_string(item.url or "")}"')
            headers, skipped = _literal_values(
                item.headers, resolver=resolver, owner=owner
            )
            warnings.extend(f"{item.id}: {value}" for value in skipped)
            if headers:
                lines.append(f"[mcp_servers.{_toml_key(item.id)}.http_headers]")
                for key, value in sorted(headers.items()):
                    lines.append(f'{_toml_key(key)} = "{_toml_string(value)}"')
        lines.append("")
    lines.append(f"# END {MANAGED_MARKER}")
    return "\n".join(lines), tuple(warnings)


def _json_servers(
    descriptors: Sequence[ToolServerDescriptor],
    *,
    resolver: SecretResolver | None = None,
    owner: str | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    result: dict[str, Any] = {}
    warnings: list[str] = []
    for item in descriptors:
        if item.transport is MCPTransport.STDIO:
            values, skipped = _literal_values(
                item.environment, resolver=resolver, owner=owner
            )
            entry: dict[str, Any] = {
                "command": item.command,
                "args": list(item.args),
            }
            if values:
                entry["env"] = values
        else:
            values, skipped = _literal_values(
                item.headers, resolver=resolver, owner=owner
            )
            entry = {"url": item.url, "type": "http"}
            if values:
                entry["headers"] = values
        warnings.extend(f"{item.id}: {value}" for value in skipped)
        result[item.id] = entry
    return result, tuple(warnings)


def _literal_values(
    values: Mapping[str, str | SecretReference],
    *,
    resolver: SecretResolver | None = None,
    owner: str | None = None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    literals: dict[str, str] = {}
    warnings: list[str] = []
    for key, value in values.items():
        if isinstance(value, SecretReference):
            if resolver is None or owner is None:
                warnings.append(
                    f"secret reference {key} was not copied; use an explicit secret flow"
                )
            else:
                resolved = resolver.resolve(value, owner=owner)
                literals[key] = resolved.reveal_for(owner)
        else:
            literals[key] = value
    return literals, tuple(warnings)


def _compose_resolved_managed_config(
    harness_id: str,
    current: str,
    descriptors: Sequence[ToolServerDescriptor],
    *,
    resolver: SecretResolver,
    owner: str,
) -> str:
    selected = _selected(descriptors, harness_id)
    if harness_id == "codex-cli":
        base = _remove_codex_managed_block(current).rstrip()
        block, _warnings = _codex_block(selected, resolver=resolver, owner=owner)
        content = f"{base}\n\n{block}" if base else block
        return content.rstrip() + "\n"
    try:
        parsed = json.loads(current) if current.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Managed CLI JSON config is invalid") from exc
    data = dict(parsed) if isinstance(parsed, Mapping) else {}
    entries, _warnings = _json_servers(selected, resolver=resolver, owner=owner)
    data["mcpServers"] = entries
    data["_gpt2giga"] = {"marker": MANAGED_MARKER}
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _remove_codex_managed_block(content: str) -> str:
    begin = f"# BEGIN {MANAGED_MARKER}"
    end = f"# END {MANAGED_MARKER}"
    if begin not in content:
        return content
    before, remainder = content.split(begin, 1)
    if end not in remainder:
        raise ValueError("Managed Codex MCP block is incomplete")
    _owned, after = remainder.split(end, 1)
    return f"{before.rstrip()}\n{after.lstrip()}".strip()


def _redacted_diff(before: str, after: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{name}.current",
            tofile=f"{name}.managed",
        )
    )


def _backup(path: Path, content: str) -> Path:
    backup_dir = path.parent / ".gpt2giga-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.{_content_hash(content)[:12]}.bak"
    if not backup.exists():
        _atomic_write(backup, content)
    return backup


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_ownership(home: Path, result: ManagedConfigResult) -> None:
    payload = {"marker": MANAGED_MARKER, **asdict(result)}
    _atomic_write(
        _ownership_path(home),
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _read_ownership(home: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(_ownership_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _ownership_path(home: Path) -> Path:
    return home / ".gpt2giga-mcp-owner.json"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _json_hash(value: Mapping[str, Any]) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _content_hash(content)


def _descriptor_to_snapshot(descriptor: ToolServerDescriptor) -> dict[str, Any]:
    return {
        "id": descriptor.id,
        "title": descriptor.title,
        "transport": descriptor.transport.value,
        "description": descriptor.description,
        "command": descriptor.command,
        "args": list(descriptor.args),
        "cwd": descriptor.cwd,
        "url": descriptor.url,
        "environment": _values_to_snapshot(descriptor.environment),
        "headers": _values_to_snapshot(descriptor.headers),
        "instructions": descriptor.instructions,
        "source": descriptor.source,
        "trusted": descriptor.trusted,
        "enabled": descriptor.enabled,
        "timeout_seconds": descriptor.timeout_seconds,
        "harnesses": list(descriptor.harnesses),
    }


def _descriptor_from_snapshot(value: Mapping[str, Any]) -> ToolServerDescriptor:
    try:
        descriptor = ToolServerDescriptor(
            id=str(value["id"]),
            title=str(value["title"]),
            transport=MCPTransport(str(value["transport"])),
            description=str(value.get("description") or ""),
            command=str(value["command"]) if value.get("command") else None,
            args=tuple(str(item) for item in value.get("args") or ()),
            cwd=str(value["cwd"]) if value.get("cwd") else None,
            url=str(value["url"]) if value.get("url") else None,
            environment=_values_from_snapshot(value.get("environment")),
            headers=_values_from_snapshot(value.get("headers")),
            instructions=str(value.get("instructions") or ""),
            source=str(value.get("source") or "project"),
            trusted=bool(value.get("trusted")),
            enabled=bool(value.get("enabled")),
            timeout_seconds=float(value.get("timeout_seconds") or 10.0),
            harnesses=tuple(str(item) for item in value.get("harnesses") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Managed MCP snapshot descriptor is invalid") from exc
    if not descriptor.trusted or not descriptor.enabled:
        raise ValueError(
            "Managed MCP snapshot contains an untrusted or disabled server"
        )
    return descriptor


def _values_to_snapshot(
    values: Mapping[str, str | SecretReference],
) -> dict[str, Any]:
    return {
        key: (
            {"secret_ref": secret_reference_to_dict(value)}
            if isinstance(value, SecretReference)
            else {"literal": value}
        )
        for key, value in sorted(values.items())
    }


def _values_from_snapshot(value: Any) -> dict[str, str | SecretReference]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | SecretReference] = {}
    for raw_key, raw_item in value.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_item, Mapping):
            raise ValueError("Managed MCP snapshot value is invalid")
        reference = raw_item.get("secret_ref")
        if isinstance(reference, Mapping):
            result[key] = secret_reference_from_dict(reference)
        elif "literal" in raw_item and isinstance(raw_item["literal"], str):
            result[key] = raw_item["literal"]
        else:
            raise ValueError("Managed MCP snapshot value is invalid")
    return result


def _snapshot_from_record(record: Mapping[str, Any]) -> HeadlessManagedMCPSnapshot:
    content = {
        "schema_version": record.get("schema_version"),
        "marker": record.get("marker"),
        "project_id": record.get("project_id"),
        "harness_id": record.get("harness_id"),
        "server_ids": record.get("server_ids"),
        "descriptors": record.get("descriptors"),
    }
    if content["schema_version"] != 1 or content["marker"] != HEADLESS_SNAPSHOT_MARKER:
        raise ValueError("Unsupported managed MCP snapshot schema")
    snapshot_hash = str(record.get("snapshot_hash") or "")
    if snapshot_hash != _json_hash(content):
        raise ValueError("Managed MCP snapshot integrity check failed")
    snapshot_id = str(record.get("snapshot_id") or "")
    if snapshot_id != f"mcp_{snapshot_hash[:32]}":
        raise ValueError("Managed MCP snapshot id does not match content")
    project_id = str(content["project_id"] or "")
    harness_id = str(content["harness_id"] or "")
    _validate_project_id(project_id)
    _validate_harness(harness_id)
    raw_server_ids = content["server_ids"]
    raw_descriptors = content["descriptors"]
    if not isinstance(raw_server_ids, list) or not all(
        isinstance(item, str) and item for item in raw_server_ids
    ):
        raise ValueError("Managed MCP snapshot server_ids are invalid")
    if not isinstance(raw_descriptors, list) or not all(
        isinstance(item, Mapping) for item in raw_descriptors
    ):
        raise ValueError("Managed MCP snapshot descriptors are invalid")
    descriptors = tuple(dict(item) for item in raw_descriptors)
    parsed_ids = tuple(_descriptor_from_snapshot(item).id for item in descriptors)
    if parsed_ids != tuple(raw_server_ids):
        raise ValueError("Managed MCP snapshot descriptor ids do not match")
    return HeadlessManagedMCPSnapshot(
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_hash,
        project_id=project_id,
        harness_id=harness_id,
        server_ids=tuple(raw_server_ids),
        created_at=str(record.get("created_at") or ""),
        descriptors=descriptors,
    )


def _validate_harness(harness_id: str) -> None:
    if harness_id not in SUPPORTED_HARNESSES:
        raise ValueError(f"Unsupported managed MCP harness: {harness_id}")


def _validate_project_id(project_id: str) -> None:
    if not project_id.startswith("proj_") or not project_id.replace("_", "").isalnum():
        raise ValueError("Invalid project id")


def _validate_owned_home(data_dir: Path, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(data_dir)
    except ValueError as exc:
        raise ManagedConfigOwnershipError("Path is outside Harness data dir") from exc
    return resolved


def _toml_key(value: str) -> str:
    if value.replace("_", "").replace("-", "").isalnum():
        return value
    return f'"{_toml_string(value)}"'


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
