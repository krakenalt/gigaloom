"""Atomic local registration metadata for declarative Agent Profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from gigaloom.harnesses.agent_profiles.builtins import load_builtin_agent_profiles
from gigaloom.harnesses.agent_profiles.models import (
    CoreCommandCollisionContractV1,
)
from gigaloom.harnesses.agent_profiles.registry import AgentProfileRegistry
from gigaloom.harnesses.agent_profiles.sources import load_local_agent_profile


AGENT_REGISTRATION_SCHEMA_VERSION = 1
MAX_REGISTRATION_FILE_BYTES = 2 * 1024 * 1024
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class AgentProfileRegistrationV1:
    """Digest-bound registration that grants no execution admission."""

    agent_id: str
    manifest_path: str
    profile_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.agent_id, str)
            or _IDENTITY_RE.fullmatch(self.agent_id) is None
        ):
            raise ValueError("agent profile registration id is invalid")
        if not isinstance(self.manifest_path, str):
            raise ValueError("agent profile registration path is invalid")
        path = Path(self.manifest_path)
        if not path.is_absolute() or "\x00" in self.manifest_path:
            raise ValueError("agent profile registration path is invalid")
        if (
            not isinstance(self.profile_digest, str)
            or _DIGEST_RE.fullmatch(self.profile_digest) is None
        ):
            raise ValueError("agent profile registration digest is invalid")


@dataclass(frozen=True)
class AgentProfileRegistrationStateV1:
    """Revisioned complete registration state for atomic replacement."""

    revision: int
    registrations: tuple[AgentProfileRegistrationV1, ...]
    schema_version: int = AGENT_REGISTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_REGISTRATION_SCHEMA_VERSION:
            raise ValueError("unsupported agent registration schema_version")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("agent registration revision is invalid")
        ids = tuple(item.agent_id for item in self.registrations)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("agent registrations must have unique sorted ids")


@dataclass(frozen=True)
class AgentProfileRegistryIssue:
    """One stale or unavailable registered profile excluded fail-closed."""

    agent_id: str
    code: str
    message: str


@dataclass(frozen=True)
class AgentProfileRegistrySnapshot:
    """Validated runtime registry plus content-free recovery issues."""

    registry: AgentProfileRegistry
    state_revision: int
    issues: tuple[AgentProfileRegistryIssue, ...]


@dataclass(frozen=True)
class AgentProfileRegistrationResult:
    """Preview or applied local registration mutation."""

    operation: str
    agent_id: str
    profile_digest: str
    previous_revision: int
    next_revision: int
    dry_run: bool
    changed: bool


class AgentProfileRegistrationStore:
    """Private bounded JSON store using one atomic file replacement."""

    def __init__(self, data_dir: str | Path) -> None:
        self.directory = Path(data_dir) / "agent_profiles"
        self.path = self.directory / "registrations-v1.json"

    def read(self) -> AgentProfileRegistrationStateV1:
        """Read and strictly validate the complete current state."""
        if self.path.is_symlink():
            raise ValueError("agent registration state must be a regular file")
        if not self.path.exists():
            return AgentProfileRegistrationStateV1(revision=0, registrations=())
        if not self.path.is_file():
            raise ValueError("agent registration state must be a regular file")
        file_stat = self.path.stat()
        if file_stat.st_size > MAX_REGISTRATION_FILE_BYTES:
            raise ValueError("agent registration state is too large")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("agent registration state is invalid") from exc
        return _decode_state(payload)

    def write(self, state: AgentProfileRegistrationStateV1) -> None:
        """Persist one validated complete state with private permissions."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("agent registration directory is unsafe")
        content = (
            json.dumps(
                _encode_state(state),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".registrations-",
            suffix=".tmp",
            dir=self.directory,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()


def load_agent_profile_registry(
    data_dir: str | Path,
    *,
    collision_contract: CoreCommandCollisionContractV1,
) -> AgentProfileRegistrySnapshot:
    """Load built-ins plus digest-matching registrations into bounded indexes."""
    state = AgentProfileRegistrationStore(data_dir).read()
    profiles = list(load_builtin_agent_profiles())
    issues: list[AgentProfileRegistryIssue] = []
    for registration in state.registrations:
        try:
            profile = load_local_agent_profile(registration.manifest_path)
        except ValueError as exc:
            issues.append(
                AgentProfileRegistryIssue(
                    registration.agent_id,
                    "manifest_unavailable",
                    str(exc),
                )
            )
            continue
        if profile.agent_id != registration.agent_id:
            issues.append(
                AgentProfileRegistryIssue(
                    registration.agent_id,
                    "identity_drift",
                    "registered manifest agent id changed",
                )
            )
            continue
        if profile.profile_digest != registration.profile_digest:
            issues.append(
                AgentProfileRegistryIssue(
                    registration.agent_id,
                    "profile_digest_drift",
                    "registered manifest digest changed",
                )
            )
            continue
        try:
            AgentProfileRegistry.build(
                (*profiles, profile),
                collision_contract=collision_contract,
            )
        except ValueError as exc:
            issues.append(
                AgentProfileRegistryIssue(
                    registration.agent_id,
                    "profile_collision",
                    str(exc),
                )
            )
            continue
        profiles.append(profile)
    return AgentProfileRegistrySnapshot(
        registry=AgentProfileRegistry.build(
            profiles,
            collision_contract=collision_contract,
        ),
        state_revision=state.revision,
        issues=tuple(issues),
    )


def register_local_agent_profile(
    data_dir: str | Path,
    manifest_path: str | Path,
    *,
    collision_contract: CoreCommandCollisionContractV1,
    dry_run: bool = False,
) -> AgentProfileRegistrationResult:
    """Validate and explicitly register one local manifest without executing it."""
    profile = load_local_agent_profile(manifest_path)
    builtins = {item.agent_id for item in load_builtin_agent_profiles()}
    if profile.agent_id in builtins:
        raise ValueError("built-in agent profiles cannot be overridden")
    store = AgentProfileRegistrationStore(data_dir)
    state = store.read()
    existing = {item.agent_id: item for item in state.registrations}
    current = existing.get(profile.agent_id)
    canonical_path = str(Path(manifest_path).expanduser().resolve(strict=True))
    registration = AgentProfileRegistrationV1(
        agent_id=profile.agent_id,
        manifest_path=canonical_path,
        profile_digest=profile.profile_digest,
    )
    if current is not None:
        if current == registration:
            return AgentProfileRegistrationResult(
                "register",
                profile.agent_id,
                profile.profile_digest,
                state.revision,
                state.revision,
                dry_run,
                False,
            )
        raise ValueError(
            "agent profile id is already registered; remove it before replacement"
        )
    snapshot = load_agent_profile_registry(
        data_dir,
        collision_contract=collision_contract,
    )
    if snapshot.issues:
        raise ValueError("agent registration state has unresolved stale profiles")
    AgentProfileRegistry.build(
        (*snapshot.registry.profiles, profile),
        collision_contract=collision_contract,
    )
    next_state = AgentProfileRegistrationStateV1(
        revision=state.revision + 1,
        registrations=tuple(
            sorted((*state.registrations, registration), key=lambda item: item.agent_id)
        ),
    )
    if not dry_run:
        store.write(next_state)
    return AgentProfileRegistrationResult(
        "register",
        profile.agent_id,
        profile.profile_digest,
        state.revision,
        next_state.revision,
        dry_run,
        True,
    )


def remove_registered_agent_profile(
    data_dir: str | Path,
    agent_id: str,
    *,
    dry_run: bool = False,
) -> AgentProfileRegistrationResult:
    """Remove only GigaLoom registration metadata, never provider artifacts."""
    if agent_id in {item.agent_id for item in load_builtin_agent_profiles()}:
        raise ValueError("built-in agent profiles cannot be removed")
    store = AgentProfileRegistrationStore(data_dir)
    state = store.read()
    by_id = {item.agent_id: item for item in state.registrations}
    try:
        removed = by_id[agent_id]
    except KeyError as exc:
        raise ValueError(f"agent profile is not registered: {agent_id}") from exc
    next_state = AgentProfileRegistrationStateV1(
        revision=state.revision + 1,
        registrations=tuple(
            item for item in state.registrations if item.agent_id != agent_id
        ),
    )
    if not dry_run:
        store.write(next_state)
    return AgentProfileRegistrationResult(
        "remove",
        agent_id,
        removed.profile_digest,
        state.revision,
        next_state.revision,
        dry_run,
        True,
    )


def _decode_state(value: Any) -> AgentProfileRegistrationStateV1:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "revision",
        "registrations",
    }:
        raise ValueError("agent registration state keys are invalid")
    records = value["registrations"]
    if not isinstance(records, list) or len(records) > 1_000:
        raise ValueError("agent registrations are invalid")
    registrations: list[AgentProfileRegistrationV1] = []
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {
            "agent_id",
            "manifest_path",
            "profile_digest",
        }:
            raise ValueError("agent registration record keys are invalid")
        registrations.append(
            AgentProfileRegistrationV1(
                agent_id=record["agent_id"],
                manifest_path=record["manifest_path"],
                profile_digest=record["profile_digest"],
            )
        )
    return AgentProfileRegistrationStateV1(
        schema_version=value["schema_version"],
        revision=value["revision"],
        registrations=tuple(registrations),
    )


def _encode_state(state: AgentProfileRegistrationStateV1) -> dict[str, Any]:
    return {
        "schema_version": state.schema_version,
        "revision": state.revision,
        "registrations": [
            {
                "agent_id": item.agent_id,
                "manifest_path": item.manifest_path,
                "profile_digest": item.profile_digest,
            }
            for item in state.registrations
        ],
    }
