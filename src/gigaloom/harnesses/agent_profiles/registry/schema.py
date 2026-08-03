"""Strict bounded decoder for the official ACP Registry v1 JSON document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import cast
from urllib.parse import unquote, urlsplit

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    ACPRegistrySnapshotV1,
    ACPRegistrySourceKind,
    acp_distribution_digest,
    acp_registry_entry_digest,
)
from gigaloom.harnesses.agent_profiles.registry.errors import RegistrySchemaError
from gigaloom.harnesses.agent_profiles.registry.models import (
    ACPRegistryCatalog,
    registry_entries_digest,
)


OFFICIAL_ACP_REGISTRY_URL = (
    "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
)
SUPPORTED_REGISTRY_VERSION = "1.0.0"
MAX_REGISTRY_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_REGISTRY_ENTRIES = 1_000
MAX_REGISTRY_JSON_NODES = 100_000
MAX_REGISTRY_JSON_DEPTH = 12
MAX_REGISTRY_ARGUMENTS = 64
MAX_REGISTRY_ENVIRONMENT = 64

_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")
_REGISTRY_VERSION_RE = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SUPPORTED_BINARY_TARGETS = {
    "darwin-aarch64": ("darwin", "aarch64"),
    "darwin-x86_64": ("darwin", "x86_64"),
    "linux-aarch64": ("linux", "aarch64"),
    "linux-x86_64": ("linux", "x86_64"),
    "windows-aarch64": ("windows", "aarch64"),
    "windows-x86_64": ("windows", "x86_64"),
}
_PACKAGE_SOURCES = {
    ACPDistributionKind.NPX: "https://registry.npmjs.org",
    ACPDistributionKind.UVX: "https://pypi.org",
}
_GITHUB_RELEASE_ASSET_ORIGIN = "https://release-assets.githubusercontent.com"
_GITHUB_RELEASE_ARCHIVE_RE = re.compile(r"/[^/]+/[^/]+/releases/download/[^/]+/[^/]+\Z")


def decode_registry_document(
    payload: bytes,
    *,
    fetched_at: datetime,
    source_url: str = OFFICIAL_ACP_REGISTRY_URL,
    etag: str | None = None,
    last_modified: str | None = None,
    stale: bool = False,
) -> ACPRegistryCatalog:
    """Decode inert official metadata without importing, resolving, or executing it."""
    try:
        return _decode_registry_document(
            payload,
            fetched_at=fetched_at,
            source_url=source_url,
            etag=etag,
            last_modified=last_modified,
            stale=stale,
        )
    except RegistrySchemaError:
        raise
    except (TypeError, ValueError) as error:
        raise RegistrySchemaError(str(error)) from error


def _decode_registry_document(
    payload: bytes,
    *,
    fetched_at: datetime,
    source_url: str,
    etag: str | None,
    last_modified: str | None,
    stale: bool,
) -> ACPRegistryCatalog:
    if not isinstance(payload, bytes):
        raise RegistrySchemaError("ACP registry document must be bytes")
    if not payload or len(payload) > MAX_REGISTRY_DOCUMENT_BYTES:
        raise RegistrySchemaError("ACP registry document is empty or too large")
    document = _decode_json(payload)
    _validate_json_bounds(document)
    root = _mapping(
        document,
        required={"version", "agents"},
        optional={"extensions"},
        field_name="ACP registry document",
    )
    if "extensions" in root:
        _array(
            root["extensions"],
            field_name="registry extensions",
            maximum=0,
        )
    version = _text(root["version"], field_name="registry version", maximum=32)
    if version != SUPPORTED_REGISTRY_VERSION:
        raise RegistrySchemaError("unsupported registry schema version")
    agents = _array(
        root["agents"],
        field_name="registry agents",
        maximum=MAX_REGISTRY_ENTRIES,
    )
    snapshot_digest = hashlib.sha256(payload).hexdigest()
    entries: list[ACPRegistryEntryV1] = []
    seen_ids: set[str] = set()
    for position, value in enumerate(agents):
        entry = _decode_entry(
            value,
            snapshot_digest=snapshot_digest,
            position=position,
        )
        if entry.registry_id in seen_ids:
            raise RegistrySchemaError(f"duplicate registry id: {entry.registry_id}")
        seen_ids.add(entry.registry_id)
        entries.append(entry)
    ordered = tuple(sorted(entries, key=lambda entry: entry.registry_id))
    snapshot = ACPRegistrySnapshotV1(
        source_url=source_url,
        source_kind=ACPRegistrySourceKind.OFFICIAL,
        fetched_at=fetched_at,
        etag=_optional_header(etag, field_name="registry etag"),
        last_modified=_optional_header(
            last_modified,
            field_name="registry last-modified",
        ),
        snapshot_digest=snapshot_digest,
        entry_count=len(ordered),
        entries_digest=registry_entries_digest(ordered),
        stale=stale,
    )
    return ACPRegistryCatalog(
        registry_version=version,
        snapshot=snapshot,
        entries=ordered,
    )


def _decode_entry(
    value: object,
    *,
    snapshot_digest: str,
    position: int,
) -> ACPRegistryEntryV1:
    record = _mapping(
        value,
        required={"id", "name", "version", "description", "distribution"},
        optional={
            "repository",
            "website",
            "authors",
            "license",
            "icon",
        },
        field_name=f"registry agent {position}",
    )
    registry_id = _registry_id(record["id"])
    version = _text(record["version"], field_name="agent version", maximum=128)
    if _REGISTRY_VERSION_RE.fullmatch(version) is None:
        raise RegistrySchemaError(
            f"agent {registry_id} version is not registry-compatible"
        )
    distributions = _decode_distributions(record["distribution"], registry_id)
    values = {
        "registry_id": registry_id,
        "name": _text(record["name"], field_name="agent name", maximum=256),
        "version": version,
        "description": _text(
            record["description"],
            field_name="agent description",
            maximum=4_096,
        ),
        "repository": _optional_url(record.get("repository"), "agent repository"),
        "website": _optional_url(record.get("website"), "agent website"),
        "authors": _authors(record.get("authors", [])),
        "license": _text(
            record.get("license", "unknown"),
            field_name="agent license",
            maximum=128,
        ),
        "icon_ref": _optional_url(record.get("icon"), "agent icon"),
        "distributions": distributions,
    }
    return ACPRegistryEntryV1(
        **values,
        entry_digest=acp_registry_entry_digest(**values),
        snapshot_digest=snapshot_digest,
    )


def _decode_distributions(
    value: object,
    registry_id: str,
) -> tuple[ACPDistributionV1, ...]:
    record = _mapping(
        value,
        required=set(),
        optional={"binary", "npx", "uvx"},
        field_name=f"agent {registry_id} distribution",
    )
    if not record:
        raise RegistrySchemaError(f"agent {registry_id} has no distribution")
    result: list[ACPDistributionV1] = []
    binary = record.get("binary")
    if binary is not None:
        result.extend(_decode_binary_distributions(binary, registry_id))
    for key, kind in (
        ("npx", ACPDistributionKind.NPX),
        ("uvx", ACPDistributionKind.UVX),
    ):
        package = record.get(key)
        if package is not None:
            result.append(_decode_package_distribution(package, kind, registry_id))
    return tuple(result)


def _decode_binary_distributions(
    value: object,
    registry_id: str,
) -> tuple[ACPDistributionV1, ...]:
    targets = _mapping(
        value,
        required=set(),
        optional=set(_SUPPORTED_BINARY_TARGETS),
        field_name=f"agent {registry_id} binary distribution",
    )
    if not targets:
        raise RegistrySchemaError(f"agent {registry_id} has no binary target")
    result: list[ACPDistributionV1] = []
    for target in sorted(targets):
        platform, architecture = _SUPPORTED_BINARY_TARGETS[target]
        record = _mapping(
            targets[target],
            required={"archive", "cmd"},
            optional={"sha256", "args", "env"},
            field_name=f"agent {registry_id} binary target {target}",
        )
        source = _url(record["archive"], field_name="binary archive")
        command = _relative_command(record["cmd"], platform=platform)
        expected = record.get("sha256")
        expected_integrity = None
        if expected is not None:
            expected_integrity = _sha256(expected)
        arguments = _arguments(record.get("args", []))
        environment = _environment(record.get("env", {}))
        archive_name = unquote(PurePosixPath(urlsplit(source).path).name)
        if not archive_name:
            raise RegistrySchemaError("binary archive URL has no file name")
        values = {
            "kind": ACPDistributionKind.BINARY,
            "platform": platform,
            "architecture": architecture,
            "source": source,
            "package_or_archive": archive_name,
            "expected_integrity": expected_integrity,
            "command": command,
            "arguments": arguments,
            "environment": environment,
            "network_origins": _binary_network_origins(source),
        }
        result.append(
            ACPDistributionV1(
                **values,
                distribution_digest=acp_distribution_digest(**values),
            )
        )
    return tuple(result)


def _decode_package_distribution(
    value: object,
    kind: ACPDistributionKind,
    registry_id: str,
) -> ACPDistributionV1:
    record = _mapping(
        value,
        required={"package"},
        optional={"args", "env"},
        field_name=f"agent {registry_id} {kind.value} distribution",
    )
    source = _PACKAGE_SOURCES[kind]
    values = {
        "kind": kind,
        "platform": "any",
        "architecture": "any",
        "source": source,
        "package_or_archive": _text(
            record["package"],
            field_name=f"{kind.value} package",
            maximum=1_024,
        ),
        "expected_integrity": None,
        "command": kind.value,
        "arguments": _arguments(record.get("args", [])),
        "environment": _environment(record.get("env", {})),
        "network_origins": (source,),
    }
    return ACPDistributionV1(
        **values,
        distribution_digest=acp_distribution_digest(**values),
    )


def _decode_json(payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RegistrySchemaError("ACP registry document must be UTF-8") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except RegistrySchemaError:
        raise
    except json.JSONDecodeError as error:
        raise RegistrySchemaError("ACP registry document is not valid JSON") from error


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RegistrySchemaError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise RegistrySchemaError(f"unsupported JSON constant: {value}")


def _validate_json_bounds(value: object) -> None:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_REGISTRY_JSON_NODES or depth > MAX_REGISTRY_JSON_DEPTH:
            raise RegistrySchemaError("ACP registry JSON exceeds structural bounds")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise RegistrySchemaError("ACP registry JSON key is invalid")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _mapping(
    value: object,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    field_name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RegistrySchemaError(f"{field_name} must be an object")
    typed = cast(Mapping[str, object], value)
    missing = required - set(typed)
    unknown = set(typed) - required - optional
    if missing:
        raise RegistrySchemaError(f"{field_name} is missing fields: {sorted(missing)}")
    if unknown:
        raise RegistrySchemaError(f"{field_name} has unknown fields: {sorted(unknown)}")
    return typed


def _array(value: object, *, field_name: str, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise RegistrySchemaError(f"{field_name} must be a bounded array")
    return cast(list[object], value)


def _text(value: object, *, field_name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or _CONTROL_RE.search(value)
    ):
        raise RegistrySchemaError(f"{field_name} is invalid")
    return value


def _registry_id(value: object) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise RegistrySchemaError("registry agent id is invalid")
    return value


def _optional_url(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _url(value, field_name=field_name)


def _url(value: object, *, field_name: str) -> str:
    text = _text(value, field_name=field_name, maximum=2_048)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RegistrySchemaError(f"{field_name} must be a credential-free HTTPS URL")
    return text


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _binary_network_origins(source: str) -> tuple[str, ...]:
    """Bind reviewed GitHub Release redirects into the binary distribution."""
    parsed = urlsplit(source)
    origins = {_origin(source)}
    if (
        parsed.hostname == "github.com"
        and parsed.netloc.lower() in {"github.com", "github.com:443"}
        and _GITHUB_RELEASE_ARCHIVE_RE.fullmatch(parsed.path)
    ):
        origins.add(_GITHUB_RELEASE_ASSET_ORIGIN)
    return tuple(sorted(origins))


def _authors(value: object) -> tuple[str, ...]:
    items = _array(value, field_name="agent authors", maximum=32)
    authors = tuple(
        _text(item, field_name="agent author", maximum=256) for item in items
    )
    if len(set(authors)) != len(authors):
        raise RegistrySchemaError("agent authors must be unique")
    return authors


def _arguments(value: object) -> tuple[str, ...]:
    items = _array(
        value,
        field_name="distribution arguments",
        maximum=MAX_REGISTRY_ARGUMENTS,
    )
    return tuple(
        _text(item, field_name="distribution argument", maximum=1_024) for item in items
    )


def _environment(value: object) -> tuple[tuple[str, str], ...]:
    record = _mapping(
        value,
        required=set(),
        optional=set(cast(Mapping[object, object], value))
        if isinstance(value, Mapping)
        else set(),
        field_name="distribution environment",
    )
    if len(record) > MAX_REGISTRY_ENVIRONMENT:
        raise RegistrySchemaError("distribution environment is too large")
    return tuple(
        sorted(
            (
                _text(key, field_name="environment key", maximum=128),
                _text(item, field_name="environment value", maximum=4_096),
            )
            for key, item in record.items()
        )
    )


def _relative_command(value: object, *, platform: str) -> str:
    text = _text(value, field_name="binary command", maximum=1_024)
    if any(character.isspace() for character in text) or ":" in text:
        raise RegistrySchemaError("binary command must be one relative token")
    if "\\" in text and platform != "windows":
        raise RegistrySchemaError("binary command uses a non-native path separator")
    text = text.replace("\\", "/")
    normalized = text[2:] if text.startswith("./") else text
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or normalized != path.as_posix()
    ):
        raise RegistrySchemaError("binary command must be a safe relative path")
    return normalized


def _sha256(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9A-Fa-f]{64}", value) is None:
        raise RegistrySchemaError("binary sha256 is invalid")
    return value.lower()


def _optional_header(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name=field_name, maximum=512)
