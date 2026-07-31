"""Gemini target source inspection and command primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from gigaloom.integration_packages import (
    ExtensionTargetPlugin,
    InstallationScope,
)


from typing import TYPE_CHECKING

from gigaloom.harnesses.builtins.gemini.target_contracts import (
    GEMINI_EXTENSION_TARGET_DESCRIPTOR,
    GeminiExtensionCommandError,
    GeminiExtensionCommandResult,
    GeminiExtensionInstallation,
    GeminiExtensionSource,
    GeminiExtensionSourceKind,
    MAX_GEMINI_EXTENSION_FILES,
    MAX_GEMINI_EXTENSION_FILE_BYTES,
    MAX_GEMINI_EXTENSION_OUTPUT_CHARS,
    MAX_GEMINI_EXTENSION_TOTAL_BYTES,
    _VERSION_RE,
    _absolute_path,
    _canonical_git_source,
    _validate_extension_name,
    _validate_secret_free,
)

if TYPE_CHECKING:
    from gigaloom.harnesses.builtins.gemini.target_driver import (
        GeminiExtensionTargetDriver,
    )


def gemini_extension_target_plugin(
    factory: Callable[[], GeminiExtensionTargetDriver],
) -> ExtensionTargetPlugin:
    """Build the neutral registry entry for the Gemini extension target."""
    return ExtensionTargetPlugin(
        descriptor=GEMINI_EXTENSION_TARGET_DESCRIPTOR,
        factory=factory,
    )


def gemini_extension_source_checksum(root: str | Path, extension_name: str) -> str:
    """Return the canonical checksum for one strict local extension tree."""
    return str(
        _inspect_local_source(_absolute_path(Path(root)), extension_name)["checksum"]
    )


def _inspect_local_source(root: Path, extension_name: str) -> Mapping[str, str]:
    _assert_safe_tree_root(root, label="Gemini extension source")
    manifest = _read_json(
        root / "gemini-extension.json", label="Gemini extension manifest"
    )
    name = _required_string(manifest, "name", "Gemini extension name")
    _validate_extension_name(name, "Gemini extension name")
    if name != extension_name:
        raise ValueError("Gemini extension manifest name does not match the request")
    version = _required_string(manifest, "version", "Gemini extension version")
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("Gemini extension version is invalid")
    if "migratedTo" in manifest:
        raise ValueError("Gemini extension migratedTo requires a new reviewed source")
    for key in ("contextFileName",):
        value = manifest.get(key)
        if isinstance(value, str):
            _resolve_beneath(root, value, label=f"Gemini extension {key}")
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"Gemini extension {key} is invalid")
                _resolve_beneath(root, item, label=f"Gemini extension {key}")
        elif value is not None:
            raise ValueError(f"Gemini extension {key} is invalid")
    tree_hash = _tree_checksum(root)
    return {
        "name": name,
        "version": version,
        "source_sha256": tree_hash,
        "checksum": f"sha256:{tree_hash}",
    }


def _tree_checksum(root: Path) -> str:
    _assert_safe_tree_root(root, label="Gemini extension source")
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if path.is_symlink():
            raise ValueError("Gemini extension source contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Gemini extension source contains a non-regular file")
        count += 1
        if count > MAX_GEMINI_EXTENSION_FILES:
            raise ValueError("Gemini extension source contains too many files")
        size = path.stat().st_size
        if size > MAX_GEMINI_EXTENSION_FILE_BYTES:
            raise ValueError("Gemini extension source file is too large")
        total += size
        if total > MAX_GEMINI_EXTENSION_TOTAL_BYTES:
            raise ValueError("Gemini extension source is too large")
        relative = path.relative_to(root).as_posix()
        _normalize_relative_path(relative, label="Gemini extension source path")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if count == 0:
        raise ValueError("Gemini extension source is empty")
    return digest.hexdigest()


def _installation_from_item(
    item: Mapping[str, Any], *, scope: InstallationScope, root: Path
) -> GeminiExtensionInstallation:
    name = _required_string(item, "name", "Gemini extension name")
    version = _required_string(item, "version", "Gemini extension version")
    metadata = item.get("installMetadata")
    if not isinstance(metadata, Mapping):
        raise GeminiExtensionCommandError(
            "Gemini extension discovery omitted install metadata"
        )
    source_kind = _required_string(metadata, "type", "Gemini extension source type")
    return GeminiExtensionInstallation(
        name=name,
        version=version,
        source_kind=source_kind,
        source_sha256=_source_metadata_hash(item),
        scope=scope,
        root=root,
        enabled=item.get("isActive") is True,
    )


def _native_source_matches(
    item: Mapping[str, Any], source: GeminiExtensionSource
) -> bool:
    metadata = item.get("installMetadata")
    if not isinstance(metadata, Mapping):
        return False
    native_type = metadata.get("type")
    native_source = metadata.get("source")
    if not isinstance(native_source, str):
        return False
    if source.kind is GeminiExtensionSourceKind.LOCAL:
        return native_type == "local" and _absolute_path(
            Path(native_source)
        ) == _absolute_path(Path(source.location))
    if source.kind is GeminiExtensionSourceKind.GIT:
        if native_type not in {"git", "github-release"}:
            return False
        try:
            canonical = _canonical_git_source(native_source)
        except ValueError:
            return False
        native_ref = metadata.get("ref")
        return canonical == source.location and native_ref == source.ref
    return False


def _source_metadata_hash(item: Mapping[str, Any]) -> str:
    metadata = item.get("installMetadata")
    if not isinstance(metadata, Mapping):
        raise GeminiExtensionCommandError(
            "Gemini extension discovery omitted install metadata"
        )
    safe = {
        key: value
        for key, value in metadata.items()
        if key in {"source", "type", "ref", "autoUpdate", "preRelease"}
        and isinstance(value, (str, bool))
    }
    return _json_hash(safe)


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} must be a regular file")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _resolve_beneath(root: Path, relative: str, *, label: str) -> Path:
    normalized = _normalize_relative_path(relative, label=label)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink")
    resolved = _absolute_path(current)
    if not _is_relative_to(resolved, root):
        raise ValueError(f"{label} escapes the source root")
    if not resolved.is_file():
        raise ValueError(f"{label} does not reference a regular file")
    return resolved


def _assert_safe_tree_root(root: Path, *, label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"{label} must be a regular directory")


def _normalize_relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} is invalid")
    return path.as_posix()


def _supported_gemini_version(value: str | None) -> bool:
    if value is None:
        return False
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", value)
    return match is not None and tuple(int(item) for item in match.groups()) >= (
        0,
        46,
        0,
    )


def _run_command(
    argv: tuple[str, ...],
    env: Mapping[str, str],
    cwd: Path | None,
    timeout: float,
) -> GeminiExtensionCommandResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GeminiExtensionCommandError(
            "Gemini extension native command could not complete"
        ) from exc
    return GeminiExtensionCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout[:MAX_GEMINI_EXTENSION_OUTPUT_CHARS],
        stderr=completed.stderr[:MAX_GEMINI_EXTENSION_OUTPUT_CHARS],
    )


def _isolated_env(
    config_home: Path, *, trust_workspace: bool = False
) -> dict[str, str]:
    env = {
        key: value
        for key in ("PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
        if (value := os.environ.get(key)) is not None
    }
    env.update(
        {
            "HOME": str(config_home),
            "GEMINI_CLI_HOME": str(config_home),
            "GEMINI_TELEMETRY_ENABLED": "false",
            "NO_COLOR": "1",
        }
    )
    if trust_workspace:
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    return env


def _bounded_output(result: GeminiExtensionCommandResult) -> str:
    return (result.stdout + "\n" + result.stderr)[:MAX_GEMINI_EXTENSION_OUTPUT_CHARS]


def _first_line(value: str) -> str | None:
    stripped = value.strip()
    return stripped.splitlines()[0] if stripped else None


def _required_string(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GeminiExtensionCommandError(f"{label} is missing or invalid")
    _validate_secret_free(value, label)
    return value


def _normalize_roots(values: Sequence[str | Path]) -> tuple[Path, ...]:
    return tuple(sorted({_absolute_path(Path(value)) for value in values}, key=str))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _json_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
