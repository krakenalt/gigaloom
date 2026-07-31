"""Execution-free discovery for declarative Agent Profile sources."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import re
from typing import Any

from gigaloom.harnesses.agent_profiles.codec import (
    MAX_AGENT_PROFILE_MANIFEST_BYTES,
    decode_agent_profile_toml,
)
from gigaloom.harnesses.agent_profiles.models import (
    AgentProfileSource,
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    AgentProfileV1,
)


AGENT_PROFILE_ENTRY_POINT_GROUP = "gigaloom.agent_profiles.v1"
MAX_DISCOVERED_AGENT_PROFILES = 1_000
MAX_ENTRY_POINT_VALUE_CHARS = 1_024
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class AgentProfileSourceError:
    """One content-free source failure that does not abort sibling discovery."""

    source: str
    code: str
    message: str


@dataclass(frozen=True)
class AgentProfileDiscovery:
    """Bounded profiles and failures discovered without executing providers."""

    profiles: tuple[AgentProfileV1, ...]
    errors: tuple[AgentProfileSourceError, ...]


@dataclass(frozen=True)
class InstalledAgentProfileCandidate:
    """Installed entry-point metadata that has not imported plugin code."""

    entry_point_name: str
    entry_point_value: str
    distribution_name: str
    distribution_version: str
    metadata_digest: str


@dataclass(frozen=True)
class RegistryDistributionCandidate:
    """Untrusted install metadata retained strictly as inert data."""

    distribution_id: str
    distribution_kind: str
    package: str
    version: str
    executable: str | None
    metadata_digest: str


def load_local_agent_profile(path: str | Path) -> AgentProfileV1:
    """Load one explicit regular-file manifest and bind local provenance."""
    requested = Path(path).expanduser()
    try:
        metadata_result = requested.lstat()
    except OSError as exc:
        raise ValueError(f"agent profile manifest is unavailable: {requested}") from exc
    if requested.is_symlink():
        raise ValueError("agent profile manifest cannot be a symlink")
    if not requested.is_file():
        raise ValueError("agent profile manifest must be a regular file")
    if metadata_result.st_size > MAX_AGENT_PROFILE_MANIFEST_BYTES:
        raise ValueError("agent profile manifest is too large")
    try:
        resolved = requested.resolve(strict=True)
        payload = resolved.read_bytes()
    except OSError as exc:
        raise ValueError(f"agent profile manifest is unavailable: {requested}") from exc
    profile = decode_agent_profile_toml(payload)
    if profile.source.kind is not AgentProfileSourceKind.LOCAL_MANIFEST:
        raise ValueError("local manifest must declare source kind local_manifest")
    if profile.source.trust_class is not AgentProfileTrustClass.LOCAL:
        raise ValueError("local manifest must declare source trust class local")
    if profile.source.reviewed:
        raise ValueError("local manifest cannot self-assert reviewed status")
    source = AgentProfileSource(
        kind=AgentProfileSourceKind.LOCAL_MANIFEST,
        origin=str(resolved),
        revision=profile.source.revision,
        digest=profile.profile_digest,
        trust_class=AgentProfileTrustClass.LOCAL,
        reviewed=False,
    )
    return replace(profile, source=source)


def discover_local_agent_profiles(
    directory: str | Path,
) -> AgentProfileDiscovery:
    """Load at most 1,000 sorted local manifests without following symlinks."""
    root = Path(directory).expanduser()
    if not root.exists():
        return AgentProfileDiscovery((), ())
    if root.is_symlink() or not root.is_dir():
        return AgentProfileDiscovery(
            (),
            (
                AgentProfileSourceError(
                    source=str(root),
                    code="invalid_source_directory",
                    message="agent profile source must be a directory",
                ),
            ),
        )
    candidates = tuple(sorted(root.glob("*.toml"), key=lambda item: item.name))
    if len(candidates) > MAX_DISCOVERED_AGENT_PROFILES:
        return AgentProfileDiscovery(
            (),
            (
                AgentProfileSourceError(
                    source=str(root),
                    code="source_limit_exceeded",
                    message="agent profile source contains more than 1,000 manifests",
                ),
            ),
        )
    profiles: list[AgentProfileV1] = []
    errors: list[AgentProfileSourceError] = []
    for candidate in candidates:
        try:
            profiles.append(load_local_agent_profile(candidate))
        except ValueError as exc:
            errors.append(
                AgentProfileSourceError(
                    source=str(candidate),
                    code="invalid_manifest",
                    message=str(exc),
                )
            )
    return AgentProfileDiscovery(tuple(profiles), tuple(errors))


def discover_installed_agent_profile_candidates(
    entry_points: Iterable[metadata.EntryPoint] | None = None,
) -> tuple[InstalledAgentProfileCandidate, ...]:
    """Read entry-point distribution metadata without calling ``load``."""
    selected = (
        tuple(entry_points)
        if entry_points is not None
        else tuple(metadata.entry_points(group=AGENT_PROFILE_ENTRY_POINT_GROUP))
    )
    if len(selected) > MAX_DISCOVERED_AGENT_PROFILES:
        raise ValueError("installed agent profile source is too large")
    candidates: list[InstalledAgentProfileCandidate] = []
    for entry_point in selected:
        if entry_point.group != AGENT_PROFILE_ENTRY_POINT_GROUP:
            continue
        _validate_identity(
            entry_point.name, field_name="agent profile entry point name"
        )
        value = _bounded_text(
            entry_point.value,
            field_name="agent profile entry point value",
            maximum=MAX_ENTRY_POINT_VALUE_CHARS,
        )
        distribution = entry_point.dist
        distribution_name = (
            distribution.metadata.get("Name", "unknown")
            if distribution is not None
            else "unknown"
        )
        distribution_version = (
            distribution.version if distribution is not None else "unknown"
        )
        record = {
            "distribution_name": distribution_name,
            "distribution_version": distribution_version,
            "entry_point_name": entry_point.name,
            "entry_point_value": value,
        }
        candidates.append(
            InstalledAgentProfileCandidate(
                entry_point_name=entry_point.name,
                entry_point_value=value,
                distribution_name=_bounded_text(
                    distribution_name,
                    field_name="agent profile distribution name",
                    maximum=256,
                ),
                distribution_version=_bounded_text(
                    distribution_version,
                    field_name="agent profile distribution version",
                    maximum=128,
                ),
                metadata_digest=_digest_record(record),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.entry_point_name))


def decode_registry_distribution_candidates(
    records: Sequence[Mapping[str, Any]],
) -> tuple[RegistryDistributionCandidate, ...]:
    """Decode binary/npx/uvx metadata as inert discovery data only."""
    if len(records) > MAX_DISCOVERED_AGENT_PROFILES:
        raise ValueError("agent registry snapshot is too large")
    candidates: list[RegistryDistributionCandidate] = []
    for record in records:
        expected = {"distribution_id", "kind", "package", "version", "executable"}
        if set(record) != expected:
            raise ValueError("agent registry distribution keys are invalid")
        distribution_id = record["distribution_id"]
        _validate_identity(
            distribution_id,
            field_name="agent registry distribution id",
        )
        kind = record["kind"]
        if kind not in {"binary", "npx", "uvx"}:
            raise ValueError("agent registry distribution kind is unsupported")
        package = _bounded_text(
            record["package"],
            field_name="agent registry package",
            maximum=512,
        )
        version = _bounded_text(
            record["version"],
            field_name="agent registry version",
            maximum=128,
        )
        executable_value = record["executable"]
        executable = (
            None
            if executable_value is None
            else _bounded_text(
                executable_value,
                field_name="agent registry executable",
                maximum=512,
            )
        )
        normalized = {
            "distribution_id": distribution_id,
            "executable": executable,
            "kind": kind,
            "package": package,
            "version": version,
        }
        candidates.append(
            RegistryDistributionCandidate(
                distribution_id=distribution_id,
                distribution_kind=kind,
                package=package,
                version=version,
                executable=executable,
                metadata_digest=_digest_record(normalized),
            )
        )
    return tuple(candidates)


def _digest_record(record: Mapping[str, Any]) -> str:
    payload = json.dumps(
        record,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _bounded_text(value: object, *, field_name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
        or any(ord(character) < 32 and character not in {"\t"} for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return value
