"""Portable Skill contracts and target metadata validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path, PurePosixPath
import re


PORTABLE_SKILL_SCHEMA_VERSION = 1
MAX_SKILL_FILE_BYTES = 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PROBE_OUTPUT_CHARS = 8000
SKILL_PROBE_TIMEOUT_SECONDS = 5.0
CODEX_SKILL_TARGET_ID = "codex-skill"
CLAUDE_SKILL_TARGET_ID = "claude-skill"
GEMINI_SKILL_TARGET_ID = "gemini-skill"
_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_METADATA_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?")
_CODEX_METADATA = frozenset({"dependencies", "interface", "policy"})
_CLAUDE_METADATA = frozenset(
    {
        "agent",
        "allowed-tools",
        "argument-hint",
        "context",
        "disable-model-invocation",
        "hooks",
        "model",
        "user-invocable",
        "when_to_use",
    }
)


class SkillTargetStatus(str, Enum):
    """Truthful result of one installed-CLI skill capability probe."""

    SUPPORTED = "supported"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class SkillMetadataDisposition(str, Enum):
    """Whether one provider-specific metadata field was projected."""

    APPLIED = "applied"
    UNSUPPORTED = "unsupported"


class SkillActivationMode(str, Enum):
    """Provider-owned activation behavior after filesystem discovery."""

    IMPLICIT_OR_EXPLICIT = "implicit_or_explicit"
    PROVIDER_CONSENT = "provider_consent"


class SkillDiscoveryStatus(str, Enum):
    """Exact-current state of one generated skill projection."""

    DISCOVERED = "discovered"
    ABSENT = "absent"
    DRIFTED = "drifted"
    BLOCKED = "blocked"


@dataclass(frozen=True, order=True)
class PortableSkillFile:
    """One portable supporting file relative to the skill directory."""

    relative_path: str
    content: bytes
    mode: int = 0o644

    def __post_init__(self) -> None:
        path = _normalize_relative_path(self.relative_path)
        if any(
            path == reserved or path.startswith(f"{reserved}/")
            for reserved in ("SKILL.md", "agents/openai.yaml")
        ):
            raise ValueError("portable skill file path is reserved")
        if not isinstance(self.content, bytes):
            raise TypeError("portable skill file content must be bytes")
        if len(self.content) > MAX_SKILL_FILE_BYTES:
            raise ValueError("portable skill file is too large")
        if self.mode not in {0o600, 0o644, 0o700, 0o755}:
            raise ValueError("portable skill file mode is invalid")
        object.__setattr__(self, "relative_path", path)


@dataclass(frozen=True, order=True)
class SkillMetadataField:
    """One retained provider metadata field with a JSON/YAML-safe value."""

    name: str
    value: object

    def __post_init__(self) -> None:
        if not _METADATA_KEY_RE.fullmatch(self.name):
            raise ValueError("skill metadata field name is invalid")
        object.__setattr__(self, "value", _normalize_metadata_value(self.value))


@dataclass(frozen=True, order=True)
class SkillTargetOverlay:
    """Provider-specific metadata kept separate from the portable core."""

    target_id: str
    fields: tuple[SkillMetadataField, ...]

    def __post_init__(self) -> None:
        _target_contract(self.target_id)
        fields = tuple(sorted(self.fields, key=lambda item: item.name))
        if not fields or any(
            not isinstance(item, SkillMetadataField) for item in fields
        ):
            raise ValueError("skill target overlay fields are invalid")
        names = [item.name for item in fields]
        if len(names) != len(set(names)):
            raise ValueError("skill target overlay fields must be unique")
        for field in fields:
            _validate_supported_metadata(self.target_id, field)
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True)
class PortableSkill:
    """Agent Skills-compatible core plus explicit target metadata overlays."""

    component_id: str
    name: str
    description: str
    instructions: str
    files: tuple[PortableSkillFile, ...] = ()
    overlays: tuple[SkillTargetOverlay, ...] = ()
    schema_version: int = PORTABLE_SKILL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PORTABLE_SKILL_SCHEMA_VERSION:
            raise ValueError("unsupported portable skill schema_version")
        if not _SKILL_NAME_RE.fullmatch(self.component_id):
            raise ValueError("portable skill component_id is invalid")
        if not _SKILL_NAME_RE.fullmatch(self.name) or len(self.name) > 64:
            raise ValueError("portable skill name is invalid")
        _validate_text(self.description, "portable skill description", max_chars=1024)
        _validate_text(
            self.instructions, "portable skill instructions", max_chars=100_000
        )
        files = tuple(sorted(self.files, key=lambda item: item.relative_path))
        if any(not isinstance(item, PortableSkillFile) for item in files):
            raise TypeError("portable skill files are invalid")
        paths = [item.relative_path for item in files]
        if len(paths) != len(set(paths)):
            raise ValueError("portable skill files must be unique")
        _validate_no_path_collisions(paths)
        if sum(len(item.content) for item in files) > MAX_SKILL_TOTAL_BYTES:
            raise ValueError("portable skill payload is too large")
        overlays = tuple(sorted(self.overlays, key=lambda item: item.target_id))
        if any(not isinstance(item, SkillTargetOverlay) for item in overlays):
            raise TypeError("portable skill overlays are invalid")
        target_ids = [item.target_id for item in overlays]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("portable skill target overlays must be unique")
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "overlays", overlays)


@dataclass(frozen=True, order=True)
class SkillCommandResult:
    """Bounded subprocess result returned by an injected probe runner."""

    returncode: int
    stdout: str
    stderr: str = ""


SkillCommandRunner = Callable[
    [tuple[str, ...], Mapping[str, str], Path | None, float], SkillCommandResult
]


@dataclass(frozen=True)
class SkillCapabilitySnapshot:
    """Content-free evidence that one current CLI advertises skill discovery."""

    target_id: str
    status: SkillTargetStatus
    version: str | None
    command: tuple[str, ...]
    supports_discovery: bool
    supports_activation: bool
    discovery_method: str
    activation_mode: SkillActivationMode
    reason_code: str | None = None


@dataclass(frozen=True, order=True)
class SkillMetadataReport:
    """One applied or explicitly retained unsupported metadata field."""

    target_id: str
    field_name: str
    value_sha256: str
    disposition: SkillMetadataDisposition
    reason_code: str | None = None


@dataclass(frozen=True, order=True)
class GeneratedSkillFile:
    """One deterministic target-relative output file."""

    relative_path: str
    content: bytes
    mode: int
    sha256: str


@dataclass(frozen=True)
class GeneratedSkillPackage:
    """Target projection with exact files and content-free compatibility facts."""

    target_id: str
    skill_name: str
    status: SkillTargetStatus
    files: tuple[GeneratedSkillFile, ...]
    metadata: tuple[SkillMetadataReport, ...]
    activation_mode: SkillActivationMode
    restart_required: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class SkillDiscoveryResult:
    """Content-free exact-current discovery outcome."""

    target_id: str
    skill_name: str
    status: SkillDiscoveryStatus
    relative_paths: tuple[str, ...]
    reason_code: str | None = None


@dataclass(frozen=True)
class _SkillTargetContract:
    target_id: str
    executable: str
    directory: str
    minimum_version: tuple[int, int, int]
    maximum_version_exclusive: tuple[int, int, int]
    help_tokens: tuple[str, ...]
    metadata_fields: frozenset[str]
    discovery_method: str
    activation_mode: SkillActivationMode
    restart_required: bool


_TARGETS = {
    CODEX_SKILL_TARGET_ID: _SkillTargetContract(
        target_id=CODEX_SKILL_TARGET_ID,
        executable="codex",
        directory=".agents/skills",
        minimum_version=(0, 144, 0),
        maximum_version_exclusive=(1, 0, 0),
        help_tokens=("Codex CLI",),
        metadata_fields=_CODEX_METADATA,
        discovery_method="documented_filesystem",
        activation_mode=SkillActivationMode.IMPLICIT_OR_EXPLICIT,
        restart_required=False,
    ),
    CLAUDE_SKILL_TARGET_ID: _SkillTargetContract(
        target_id=CLAUDE_SKILL_TARGET_ID,
        executable="claude",
        directory=".claude/skills",
        minimum_version=(2, 1, 0),
        maximum_version_exclusive=(3, 0, 0),
        help_tokens=("Skills still resolve", "--disable-slash-commands"),
        metadata_fields=_CLAUDE_METADATA,
        discovery_method="documented_filesystem",
        activation_mode=SkillActivationMode.IMPLICIT_OR_EXPLICIT,
        restart_required=False,
    ),
    GEMINI_SKILL_TARGET_ID: _SkillTargetContract(
        target_id=GEMINI_SKILL_TARGET_ID,
        executable="gemini",
        directory=".gemini/skills",
        minimum_version=(0, 46, 0),
        maximum_version_exclusive=(1, 0, 0),
        help_tokens=("skills <command>", "Manage agent skills"),
        metadata_fields=frozenset(),
        discovery_method="native_cli_list",
        activation_mode=SkillActivationMode.PROVIDER_CONSENT,
        restart_required=False,
    ),
}


def _normalize_metadata_value(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        normalized = value
    elif isinstance(value, (list, tuple)):
        normalized = [_normalize_metadata_value(item) for item in value]
    elif isinstance(value, Mapping):
        normalized = {
            str(key): _normalize_metadata_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
        if any(not _METADATA_KEY_RE.fullmatch(key) for key in normalized):
            raise ValueError("skill metadata mapping key is invalid")
    else:
        raise ValueError("skill metadata value is invalid")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 32_000:
        raise ValueError("skill metadata value is too large")
    return normalized


def _validate_supported_metadata(
    target_id: str,
    field: SkillMetadataField,
) -> None:
    if target_id == CODEX_SKILL_TARGET_ID and field.name in _CODEX_METADATA:
        if not isinstance(field.value, Mapping):
            raise ValueError("Codex skill metadata field must be a mapping")
        return
    if target_id != CLAUDE_SKILL_TARGET_ID or field.name not in _CLAUDE_METADATA:
        return
    if field.name in {"disable-model-invocation", "user-invocable"}:
        if not isinstance(field.value, bool):
            raise ValueError("Claude skill boolean metadata is invalid")
        return
    if field.name == "hooks":
        if not isinstance(field.value, Mapping):
            raise ValueError("Claude skill hooks metadata is invalid")
        return
    if field.name == "allowed-tools":
        if isinstance(field.value, str):
            return
        if not isinstance(field.value, list) or any(
            not isinstance(item, str) or not item for item in field.value
        ):
            raise ValueError("Claude skill allowed-tools metadata is invalid")
        return
    if not isinstance(field.value, str) or not field.value:
        raise ValueError("Claude skill text metadata is invalid")


def _validate_no_path_collisions(paths: list[str]) -> None:
    path_set = set(paths)
    for value in paths:
        current = PurePosixPath(value)
        for parent in current.parents:
            if parent == PurePosixPath("."):
                break
            if parent.as_posix() in path_set:
                raise ValueError("portable skill file paths collide")


def _normalize_relative_path(value: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("portable skill path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("portable skill path must remain relative")
    return path.as_posix()


def _validate_text(value: str, field_name: str, *, max_chars: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise ValueError(f"{field_name} is invalid")
    if "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _target_contract(target_id: str) -> _SkillTargetContract:
    try:
        return _TARGETS[target_id]
    except KeyError as exc:
        raise ValueError("unknown portable skill target") from exc
