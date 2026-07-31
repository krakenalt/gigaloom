"""Portable Skill capability, installation, and discovery operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

from gigaloom.integration_installer import (
    FileInstallMutation,
    InstallationPlan,
    InstallationRequest,
    InstallationTarget,
)
from gigaloom.integration_packages import (
    InstallationScope,
    IntegrationComponentType,
    IntegrationPackage,
)
from gigaloom.skills.authoring import _project_skill_content
from gigaloom.skills.portable_models import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    MAX_PROBE_OUTPUT_CHARS,
    SKILL_PROBE_TIMEOUT_SECONDS,
    GeneratedSkillPackage,
    PortableSkill,
    SkillCapabilitySnapshot,
    SkillCommandResult,
    SkillCommandRunner,
    SkillDiscoveryResult,
    SkillDiscoveryStatus,
    SkillTargetStatus,
    _SkillTargetContract,
    _VERSION_RE,
    _target_contract,
)
from gigaloom.types import redact_secrets


def probe_skill_target(
    target_id: str,
    *,
    command: tuple[str, ...] | None = None,
    runner: SkillCommandRunner | None = None,
) -> SkillCapabilitySnapshot:
    """Probe one installed CLI without provider traffic or a real native home."""
    contract = _target_contract(target_id)
    resolved_command = command or (contract.executable,)
    run = runner or _run_command
    with tempfile.TemporaryDirectory(prefix="gpt2giga-skill-probe-") as raw:
        probe_root = Path(raw)
        env = _isolated_env(target_id, probe_root)
        version_result = run(
            (*resolved_command, "--version"), env, None, SKILL_PROBE_TIMEOUT_SECONDS
        )
        help_result = run(
            (*resolved_command, "--help"), env, None, SKILL_PROBE_TIMEOUT_SECONDS
        )
    version_text = _bounded_output(version_result)
    version = _parse_version(version_text)
    if version_result.returncode != 0 or help_result.returncode != 0 or version is None:
        return _capability_failure(contract, resolved_command, "probe_failed")
    if not (contract.minimum_version <= version < contract.maximum_version_exclusive):
        return _capability_failure(
            contract,
            resolved_command,
            "unsupported_version",
            version=_format_version(version),
        )
    help_text = _bounded_output(help_result)
    if not all(
        token.casefold() in help_text.casefold() for token in contract.help_tokens
    ):
        return SkillCapabilitySnapshot(
            target_id=target_id,
            status=SkillTargetStatus.DEGRADED,
            version=_format_version(version),
            command=resolved_command,
            supports_discovery=False,
            supports_activation=False,
            discovery_method=contract.discovery_method,
            activation_mode=contract.activation_mode,
            reason_code="skill_surface_not_advertised",
        )
    return SkillCapabilitySnapshot(
        target_id=target_id,
        status=SkillTargetStatus.SUPPORTED,
        version=_format_version(version),
        command=resolved_command,
        supports_discovery=True,
        supports_activation=True,
        discovery_method=contract.discovery_method,
        activation_mode=contract.activation_mode,
    )


def build_skill_installation_request(
    package: IntegrationPackage,
    skill: PortableSkill,
    generated: GeneratedSkillPackage,
    *,
    scope: InstallationScope,
    root: str | Path,
) -> InstallationRequest:
    """Bind one supported projection to the existing transactional installer."""
    if generated.status is not SkillTargetStatus.SUPPORTED:
        raise ValueError("skill target capability is not supported")
    contract = _target_contract(generated.target_id)
    expected_files, expected_metadata = _project_skill_content(skill, contract)
    if (
        generated.skill_name != skill.name
        or generated.files != expected_files
        or generated.metadata != expected_metadata
        or generated.activation_mode is not contract.activation_mode
        or generated.restart_required != contract.restart_required
        or generated.reason_code is not None
    ):
        raise ValueError("generated skill package does not match the portable skill")
    if scope not in package.scopes:
        raise ValueError("integration package does not allow the requested scope")
    component = next(
        (item for item in package.components if item.id == skill.component_id),
        None,
    )
    if (
        component is None
        or component.type is not IntegrationComponentType.SKILL
        or not component.portable
    ):
        raise ValueError("integration package does not contain the portable skill")
    compatibility = next(
        (
            item
            for item in package.compatibility
            if item.target_id == generated.target_id
        ),
        None,
    )
    if (
        compatibility is None
        or "skill.discovery" not in compatibility.required_capabilities
    ):
        raise ValueError(
            "integration package does not declare skill discovery compatibility"
        )
    overlay = next(
        (item for item in package.overlays if item.target_id == generated.target_id),
        None,
    )
    if overlay is not None and skill.component_id not in overlay.component_ids:
        raise ValueError("integration target overlay omits the portable skill")
    target = InstallationTarget(
        id=generated.target_id,
        scope=scope,
        root=Path(root),
        owner_id=f"{package.id}:{skill.name}",
    )
    return InstallationRequest(
        package=package,
        target=target,
        mutations=tuple(
            FileInstallMutation(
                relative_path=item.relative_path,
                content=item.content,
                mode=item.mode,
            )
            for item in generated.files
        ),
    )


def discover_generated_skill(
    generated: GeneratedSkillPackage,
    root: str | Path,
) -> SkillDiscoveryResult:
    """Discover an exact target package without reading unrelated native state."""
    if generated.status is not SkillTargetStatus.SUPPORTED:
        return SkillDiscoveryResult(
            target_id=generated.target_id,
            skill_name=generated.skill_name,
            status=SkillDiscoveryStatus.BLOCKED,
            relative_paths=(),
            reason_code=generated.reason_code or "skill_target_not_supported",
        )
    base = Path(root)
    found: list[str] = []
    for item in generated.files:
        path = base / PurePosixPath(item.relative_path)
        if not path.exists():
            return SkillDiscoveryResult(
                target_id=generated.target_id,
                skill_name=generated.skill_name,
                status=SkillDiscoveryStatus.ABSENT,
                relative_paths=tuple(found),
                reason_code="skill_file_missing",
            )
        if _path_is_unsafe(base, PurePosixPath(item.relative_path)):
            return SkillDiscoveryResult(
                target_id=generated.target_id,
                skill_name=generated.skill_name,
                status=SkillDiscoveryStatus.DRIFTED,
                relative_paths=tuple(found),
                reason_code="skill_file_unsafe",
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
            return SkillDiscoveryResult(
                target_id=generated.target_id,
                skill_name=generated.skill_name,
                status=SkillDiscoveryStatus.DRIFTED,
                relative_paths=tuple(found),
                reason_code="skill_file_drifted",
            )
        found.append(item.relative_path)
    return SkillDiscoveryResult(
        target_id=generated.target_id,
        skill_name=generated.skill_name,
        status=SkillDiscoveryStatus.DISCOVERED,
        relative_paths=tuple(found),
    )


def generated_skill_verifier(
    generated: GeneratedSkillPackage,
) -> Callable[[Path, InstallationPlan], bool]:
    """Return an installer verifier bound to the exact generated file set."""
    expected_paths = tuple(item.relative_path for item in generated.files)

    def verify(root: Path, plan: InstallationPlan) -> bool:
        planned_paths = tuple(item.relative_path for item in plan.mutations)
        result = discover_generated_skill(generated, root)
        return (
            planned_paths == expected_paths
            and result.status is SkillDiscoveryStatus.DISCOVERED
        )

    return verify


def _path_is_unsafe(base: Path, relative_path: PurePosixPath) -> bool:
    if base.is_symlink() or not base.is_dir():
        return True
    current = base
    for part in relative_path.parts[:-1]:
        current /= part
        if current.is_symlink() or not current.is_dir():
            return True
    target = current / relative_path.name
    return target.is_symlink() or not target.is_file()


def _capability_failure(
    contract: _SkillTargetContract,
    command: tuple[str, ...],
    reason_code: str,
    *,
    version: str | None = None,
) -> SkillCapabilitySnapshot:
    return SkillCapabilitySnapshot(
        target_id=contract.target_id,
        status=SkillTargetStatus.BLOCKED,
        version=version,
        command=command,
        supports_discovery=False,
        supports_activation=False,
        discovery_method=contract.discovery_method,
        activation_mode=contract.activation_mode,
        reason_code=reason_code,
    )


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(value)
    if match is None:
        return None
    return tuple(int(item or 0) for item in match.groups())  # type: ignore[return-value]


def _format_version(value: tuple[int, int, int]) -> str:
    return ".".join(str(item) for item in value)


def _bounded_output(result: SkillCommandResult) -> str:
    output = f"{result.stdout}\n{result.stderr}"[:MAX_PROBE_OUTPUT_CHARS]
    return str(redact_secrets(output))


def _isolated_env(target_id: str, root: Path) -> dict[str, str]:
    env = {"HOME": str(root), "PATH": os.environ.get("PATH", "")}
    if target_id == CODEX_SKILL_TARGET_ID:
        env["CODEX_HOME"] = str(root / ".codex")
    elif target_id == CLAUDE_SKILL_TARGET_ID:
        env["CLAUDE_CONFIG_DIR"] = str(root / ".claude")
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    else:
        env["GEMINI_CLI_HOME"] = str(root)
    return env


def _run_command(
    argv: tuple[str, ...],
    env: Mapping[str, str],
    cwd: Path | None,
    timeout: float,
) -> SkillCommandResult:
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
        return SkillCommandResult(
            returncode=127,
            stdout="",
            stderr=f"{type(exc).__name__} (details omitted)",
        )
    return SkillCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout[:MAX_PROBE_OUTPUT_CHARS],
        stderr=completed.stderr[:MAX_PROBE_OUTPUT_CHARS],
    )
