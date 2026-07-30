"""Claude target source inspection and command primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from gpt2giga_harness.integration_packages import (
    ExtensionTargetPlugin,
    InstallationScope,
)


from typing import TYPE_CHECKING

from gpt2giga_harness.harnesses.builtins.claude.target_contracts import (
    CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
    ClaudePluginCommandError,
    ClaudePluginCommandResult,
    ClaudePluginInstallation,
    ClaudePluginSource,
    ClaudePluginSourceKind,
    MAX_CLAUDE_PLUGIN_FILES,
    MAX_CLAUDE_PLUGIN_FILE_BYTES,
    MAX_CLAUDE_PLUGIN_OUTPUT_CHARS,
    MAX_CLAUDE_PLUGIN_TOTAL_BYTES,
    _GITHUB_SHORTHAND_RE,
    _VERSION_RE,
    _absolute_path,
    _normalize_relative_path,
    _validate_plugin_name,
    _validate_secret_free,
)

if TYPE_CHECKING:
    from gpt2giga_harness.harnesses.builtins.claude.target_driver import (
        ClaudePluginTargetDriver,
    )


def claude_plugin_target_plugin(
    factory: Callable[[], ClaudePluginTargetDriver],
) -> ExtensionTargetPlugin:
    """Build a neutral runtime registration for a configured Claude target."""
    return ExtensionTargetPlugin(
        descriptor=CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
        factory=factory,
    )


def claude_plugin_source_checksum(
    marketplace_root: str | Path,
    marketplace_name: str,
    plugin_name: str,
) -> str:
    """Return the deterministic package checksum for one local marketplace entry."""
    source = ClaudePluginSource(
        marketplace_name=marketplace_name,
        kind=ClaudePluginSourceKind.LOCAL,
        location=str(_absolute_path(Path(marketplace_root))),
    )
    inspection = _inspect_local_source(
        _absolute_path(Path(marketplace_root)), source, plugin_name
    )
    return str(inspection["checksum"])


def _inspect_local_source(
    root: Path,
    source: ClaudePluginSource,
    plugin_name: str,
) -> Mapping[str, Any]:
    _assert_safe_tree_root(root, label="Claude marketplace root")
    marketplace = _read_json(
        root / ".claude-plugin" / "marketplace.json",
        label="Claude marketplace manifest",
    )
    if marketplace.get("name") != source.marketplace_name:
        raise ValueError("Claude marketplace manifest name does not match the source")
    _required_string(marketplace, "description", "Claude marketplace description")
    owner = marketplace.get("owner")
    if not isinstance(owner, Mapping):
        raise ValueError("Claude marketplace owner is required")
    _required_string(owner, "name", "Claude marketplace owner name")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("Claude marketplace plugins must be a list")
    matches = [
        item
        for item in plugins
        if isinstance(item, Mapping) and item.get("name") == plugin_name
    ]
    if len(matches) != 1:
        raise ValueError("Claude marketplace must contain one exact plugin entry")
    entry = matches[0]
    relative_source = _local_plugin_path(entry)
    plugin_root = _resolve_beneath(root, relative_source, label="Claude plugin source")
    _assert_safe_tree_root(plugin_root, label="Claude plugin source")
    manifest = _read_json(
        plugin_root / ".claude-plugin" / "plugin.json",
        label="Claude plugin manifest",
    )
    if manifest.get("name") != plugin_name:
        raise ValueError("Claude plugin manifest name does not match the marketplace")
    version = _required_string(manifest, "version", "Claude plugin manifest version")
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("Claude plugin manifest version is invalid")
    _required_string(manifest, "description", "Claude plugin description")
    author = manifest.get("author")
    if not isinstance(author, Mapping):
        raise ValueError("Claude plugin author is required")
    _required_string(author, "name", "Claude plugin author name")
    _validate_manifest_paths(plugin_root, manifest)
    checksum = _tree_checksum(plugin_root)
    return {
        "version": version,
        "checksum": checksum,
        "source_sha256": _json_hash(
            {
                "marketplace_name": source.marketplace_name,
                "plugin_name": plugin_name,
                "marketplace": marketplace,
                "entry": entry,
                "checksum": checksum,
            }
        ),
    }


def _local_plugin_path(entry: Mapping[str, Any]) -> str:
    source = entry.get("source")
    if not isinstance(source, str) or not source.startswith("./"):
        raise ValueError("Claude local marketplace plugin source must start with ./")
    return _normalize_relative_path(source, label="Claude marketplace plugin source")


def _validate_manifest_paths(root: Path, manifest: Mapping[str, Any]) -> None:
    for field_name in (
        "skills",
        "commands",
        "agents",
        "hooks",
        "mcpServers",
        "lspServers",
    ):
        value = manifest.get(field_name)
        if value is None or isinstance(value, Mapping):
            continue
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(item, str) for item in values):
            raise ValueError(f"Claude plugin {field_name} paths are invalid")
        for item in values:
            if item.startswith("./"):
                _resolve_beneath(root, item, label=f"Claude plugin {field_name}")
            elif "/" in item or item.startswith("."):
                raise ValueError(f"Claude plugin {field_name} path is invalid")


def _tree_checksum(root: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Claude plugin source cannot contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Claude plugin source contains a non-regular file")
        files.append(path)
    if not files or len(files) > MAX_CLAUDE_PLUGIN_FILES:
        raise ValueError("Claude plugin source file count is invalid")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        data = path.read_bytes()
        total += len(data)
        if (
            len(data) > MAX_CLAUDE_PLUGIN_FILE_BYTES
            or total > MAX_CLAUDE_PLUGIN_TOTAL_BYTES
        ):
            raise ValueError("Claude plugin source size is invalid")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def _installation_from_item(
    item: Mapping[str, Any],
    root: Path,
    scope: InstallationScope,
) -> ClaudePluginInstallation:
    plugin_id = _required_string(item, "id", "Claude plugin id")
    name, marketplace_name = _split_selector(plugin_id)
    version = _required_string(item, "version", "Claude plugin version")
    enabled = item.get("enabled")
    if not isinstance(enabled, bool):
        raise ClaudePluginCommandError("Claude plugin enabled state is invalid")
    return ClaudePluginInstallation(
        plugin_id=plugin_id,
        name=name,
        marketplace_name=marketplace_name,
        version=version,
        scope=scope,
        root=root,
        enabled=enabled,
    )


def _marketplace_source_matches(
    item: Mapping[str, Any], source: ClaudePluginSource
) -> bool:
    if source.kind is ClaudePluginSourceKind.LOCAL:
        if item.get("source") != "directory":
            return False
        expected = _absolute_path(Path(source.location))
        paths = [item.get("path"), item.get("installLocation")]
        return all(
            isinstance(value, str) and _absolute_path(Path(value)) == expected
            for value in paths
        )
    expected_arg = _marketplace_source_arg(source)
    candidates = {
        value
        for key in ("sourceLocation", "url", "repo", "path", "installLocation")
        if isinstance((value := item.get(key)), str)
    }
    if expected_arg not in candidates and not (
        source.location in candidates and item.get("ref") == source.ref
    ):
        return False
    sparse = item.get("sparse")
    if source.sparse:
        return isinstance(sparse, (list, tuple)) and tuple(sparse) == source.sparse
    return sparse in (None, [], ())


def _marketplace_source_arg(source: ClaudePluginSource) -> str:
    if source.kind is ClaudePluginSourceKind.LOCAL:
        return str(_absolute_path(Path(source.location)))
    separator = "@" if _GITHUB_SHORTHAND_RE.fullmatch(source.location) else "#"
    return f"{source.location}{separator}{source.ref}"


def _split_selector(value: str) -> tuple[str, str]:
    if value.count("@") != 1:
        raise ClaudePluginCommandError("Claude plugin id is invalid")
    name, marketplace = value.split("@", 1)
    _validate_plugin_name(name, "Claude plugin name")
    _validate_plugin_name(marketplace, "Claude marketplace name")
    return name, marketplace


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is missing or unsafe")
    if path.stat().st_size > MAX_CLAUDE_PLUGIN_FILE_BYTES:
        raise ValueError(f"{label} is too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")
    return payload


def _resolve_beneath(root: Path, relative: str, *, label: str) -> Path:
    normalized = _normalize_relative_path(relative, label=label)
    resolved = _absolute_path(root / normalized)
    if not _is_relative_to(resolved, root):
        raise ValueError(f"{label} escapes the marketplace root")
    cursor = root
    for part in PurePosixPath(normalized).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink")
    return resolved


def _assert_safe_tree_root(root: Path, *, label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"{label} must be an existing regular directory")


def _supported_claude_version(value: str | None) -> bool:
    if value is None:
        return False
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", value)
    if match is None:
        return False
    return tuple(int(item) for item in match.groups()) >= (2, 1, 143)


def _run_command(
    argv: tuple[str, ...],
    env: Mapping[str, str],
    cwd: Path | None,
    timeout: float,
) -> ClaudePluginCommandResult:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            env=dict(env),
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudePluginCommandError(
            f"Claude command failed with {type(exc).__name__}"
        ) from exc
    return ClaudePluginCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout[-MAX_CLAUDE_PLUGIN_OUTPUT_CHARS:],
        stderr=completed.stderr[-MAX_CLAUDE_PLUGIN_OUTPUT_CHARS:],
    )


def _isolated_env(config_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key in ("PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
        if (value := os.environ.get(key)) is not None
    }
    env.update(
        {
            "HOME": str(config_dir.parent),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "NO_COLOR": "1",
        }
    )
    return env


def _bounded_output(result: ClaudePluginCommandResult) -> str:
    return f"{result.stdout}\n{result.stderr}"[-MAX_CLAUDE_PLUGIN_OUTPUT_CHARS:]


def _first_line(value: str) -> str | None:
    lines = value.strip().splitlines()
    return lines[0][:200] if lines else None


def _required_string(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{label} is invalid")
    _validate_secret_free(value, label)
    return value


def _normalize_roots(values: Sequence[str | Path]) -> tuple[Path, ...]:
    return tuple(sorted({_absolute_path(Path(item)) for item in values}, key=str))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
