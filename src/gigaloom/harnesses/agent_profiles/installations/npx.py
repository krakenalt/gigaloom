"""Exact npm resolution and isolated-prefix ACP agent installation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import cast
from uuid import uuid4

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    AgentInstallPlanV1,
    AgentLifecycleScriptPolicy,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
)
from gigaloom.contracts.agent_installation_codec import managed_agent_artifact_to_dict
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.commands import (
    PackageCommand,
    PackageCommandRunner,
    private_command_environment,
)
from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
    require_within,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionResolutionV1,
)
from gigaloom.harnesses.agent_profiles.installations.packages import (
    MAX_PACKAGE_LOCK_BYTES,
    NpxPackageResolution,
    PackageInstallResult,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


_EXACT_NPX_RE = re.compile(
    r"(?P<name>(?:@[a-z0-9._~-]+/)?[a-z0-9._~-]+)@"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\Z"
)
MAX_NPM_LOCK_NODES = 100_000
MAX_NPM_LOCK_DEPTH = 12


class NpxPackageResolver:
    """Resolve one exact registry package into canonical lock/integrity evidence."""

    def __init__(
        self,
        data_root: str | Path,
        npm_executable: str | Path,
        runner: PackageCommandRunner,
    ) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._npm = _absolute_executable(npm_executable, "npm")
        self._runner = runner

    def resolve(self, distribution: ACPDistributionV1) -> NpxPackageResolution:
        """Create only a private package-lock; do not install node_modules."""
        if distribution.kind is not ACPDistributionKind.NPX:
            raise AgentInstallError("npx_distribution_required")
        package_name, version = _exact_package(distribution.package_or_archive)
        root = self._data_root / "agents" / "resolution" / "npm"
        ensure_private_directory(root)
        working = root / f"resolve-{uuid4().hex}"
        ensure_private_directory(working)
        try:
            _write_package_json(working / "package.json", package_name, version)
            home = working / "home"
            ensure_private_directory(home)
            environment = _npm_environment(self._npm, home, working / "cache")
            command = PackageCommand(
                argv=(
                    str(self._npm),
                    "install",
                    "--package-lock-only",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--save-exact",
                    "--prefix",
                    str(working),
                    distribution.package_or_archive,
                ),
                cwd=str(working),
                environment=environment,
            )
            result = self._runner.run(command)
            if result.return_code != 0:
                raise AgentInstallError("npm_resolution_failed")
            raw = _read_bounded(working / "package-lock.json")
            canonical, integrity = _validate_npm_lock(raw, package_name, version)
            lock_digest = hashlib.sha256(canonical).hexdigest()
            return NpxPackageResolution(
                evidence=DistributionResolutionV1(
                    distribution_digest=distribution.distribution_digest,
                    artifact_digest=lock_digest,
                    package_integrity=integrity,
                    lock_digest=lock_digest,
                ),
                package=distribution.package_or_archive,
                package_name=package_name,
                version=version,
                lock_bytes=canonical,
            )
        finally:
            shutil.rmtree(working, ignore_errors=True)


class NpxAgentInstaller:
    """Run npm ci inside one immutable managed prefix, never globally."""

    def __init__(
        self,
        data_root: str | Path,
        npm_executable: str | Path,
        runner: PackageCommandRunner,
        *,
        clock=None,  # noqa: ANN001
    ) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._npm = _absolute_executable(npm_executable, "npm")
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def install(
        self,
        plan: AgentInstallPlanV1,
        resolution: NpxPackageResolution,
        *,
        confirmed: bool,
        allow_lifecycle_scripts: bool = False,
    ) -> PackageInstallResult:
        """Install a lock-bound package into the plan's private managed root."""
        self._validate_plan(
            plan,
            resolution,
            confirmed=confirmed,
            allow_lifecycle_scripts=allow_lifecycle_scripts,
        )
        staging = require_within(
            Path(plan.staging_root),
            self._data_root / "agents" / "staging",
            reason_code="npx_staging_root_outside_authority",
        )
        if staging.name != plan.plan_id:
            raise AgentInstallError("npx_staging_root_not_plan_scoped")
        managed = require_within(
            Path(plan.managed_root),
            self._data_root / "agents" / "registry",
            reason_code="npx_managed_root_outside_authority",
        )
        ensure_private_directory(self._data_root / "agents" / "staging")
        with registry_cache_lock(self._data_root / "agents" / ".install.lock"):
            if staging.exists():
                shutil.rmtree(staging)
            ensure_private_directory(staging)
            payload = staging / "payload"
            ensure_private_directory(payload)
            try:
                return self._install_locked(
                    plan,
                    resolution,
                    payload,
                    managed,
                    allow_lifecycle_scripts=allow_lifecycle_scripts,
                )
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def _install_locked(
        self,
        plan: AgentInstallPlanV1,
        resolution: NpxPackageResolution,
        payload: Path,
        managed: Path,
        *,
        allow_lifecycle_scripts: bool,
    ) -> PackageInstallResult:
        _write_package_json(
            payload / "package.json",
            resolution.package_name,
            resolution.version,
        )
        (payload / "package-lock.json").write_bytes(resolution.lock_bytes)
        (payload / "package-lock.json").chmod(0o600)
        home = payload / ".home"
        ensure_private_directory(home)
        argv = [
            str(self._npm),
            "ci",
            "--no-audit",
            "--no-fund",
            "--prefix",
            str(payload),
        ]
        if not allow_lifecycle_scripts:
            argv.insert(2, "--ignore-scripts")
        command = PackageCommand(
            argv=tuple(argv),
            cwd=str(payload),
            environment=_npm_environment(self._npm, home, payload / ".npm-cache"),
        )
        result = self._runner.run(command)
        if result.return_code != 0:
            raise AgentInstallError("npm_install_failed")
        current_lock, integrity = _validate_npm_lock(
            _read_bounded(payload / "package-lock.json"),
            resolution.package_name,
            resolution.version,
        )
        if (
            current_lock != resolution.lock_bytes
            or integrity != plan.expected_integrity
        ):
            raise AgentInstallError("npm_integrity_mismatch")
        executable = _resolve_npm_entrypoint(payload, resolution)
        install_id = (
            "install-"
            + canonical_digest(
                {
                    "plan_id": plan.plan_id,
                    "lock_digest": resolution.evidence.lock_digest,
                }
            )[:24]
        )
        artifact = ManagedAgentArtifactV1(
            install_id=install_id,
            registry_id=plan.registry_id,
            local_agent_id=plan.local_agent_id,
            version=plan.version,
            distribution_kind=plan.distribution_kind,
            platform=plan.platform,
            artifact_digest=resolution.evidence.artifact_digest,
            package_integrity=integrity,
            lock_digest=cast(str, resolution.evidence.lock_digest),
            managed_root=str(managed),
            executable_relative_path=executable,
            command=Path(executable).name,
            arguments=plan.arguments,
            environment=plan.environment,
            installed_at=self._now(),
            status=ManagedAgentStatus.STAGED,
        )
        atomic_write_json(
            payload / ".artifact.json", managed_agent_artifact_to_dict(artifact)
        )
        evidence_digest = canonical_digest(
            {
                "argv": list(command.argv),
                "lock_digest": resolution.evidence.lock_digest,
                "stdout_digest": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_digest": hashlib.sha256(result.stderr).hexdigest(),
                "lifecycle_scripts": allow_lifecycle_scripts,
            }
        )
        install_result = PackageInstallResult(
            artifact=artifact,
            lock_digest=cast(str, resolution.evidence.lock_digest),
            package_integrity=integrity,
            command_evidence_digest=evidence_digest,
        )
        ensure_private_directory(managed.parent)
        if managed.exists():
            raise AgentInstallError("managed_artifact_already_exists")
        os.replace(payload, managed)
        return install_result

    def _validate_plan(
        self,
        plan: AgentInstallPlanV1,
        resolution: NpxPackageResolution,
        *,
        confirmed: bool,
        allow_lifecycle_scripts: bool,
    ) -> None:
        if plan.distribution_kind is not ACPDistributionKind.NPX:
            raise AgentInstallError("npx_plan_required")
        if plan.confirmation_required and not confirmed:
            raise AgentInstallError("npx_confirmation_required")
        if plan.expires_at < self._now():
            raise AgentInstallError("npx_plan_expired")
        if (
            plan.package_or_archive != resolution.package
            or plan.expected_integrity != resolution.evidence.package_integrity
            or not plan.managed_root.endswith(resolution.evidence.artifact_digest)
        ):
            raise AgentInstallError("npx_resolution_not_bound_to_plan")
        reviewed = plan.lifecycle_script_policy is AgentLifecycleScriptPolicy.REVIEWED
        if allow_lifecycle_scripts and not reviewed:
            raise AgentInstallError("npm_lifecycle_capability_required")
        if reviewed and not allow_lifecycle_scripts:
            raise AgentInstallError("npm_lifecycle_confirmation_required")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("npx installer clock must be timezone-aware")
        return value


def _validate_npm_lock(
    raw: bytes,
    package_name: str,
    version: str,
) -> tuple[bytes, str]:
    if not raw or len(raw) > MAX_PACKAGE_LOCK_BYTES:
        raise AgentInstallError("npm_lock_invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentInstallError("npm_lock_invalid") from error
    _validate_json_bounds(value)
    if not isinstance(value, dict) or value.get("lockfileVersion") not in {2, 3}:
        raise AgentInstallError("npm_lock_invalid")
    packages = value.get("packages")
    if not isinstance(packages, dict):
        raise AgentInstallError("npm_lock_invalid")
    root = packages.get("")
    target = packages.get(f"node_modules/{package_name}")
    if not isinstance(root, dict) or not isinstance(target, dict):
        raise AgentInstallError("npm_lock_target_missing")
    dependencies = root.get("dependencies")
    if not isinstance(dependencies, dict) or dependencies.get(package_name) != version:
        raise AgentInstallError("npm_lock_target_not_exact")
    integrity = target.get("integrity")
    if target.get("version") != version or not isinstance(integrity, str):
        raise AgentInstallError("npm_lock_target_not_exact")
    for package in packages.values():
        if not isinstance(package, dict) or package.get("link") is True:
            raise AgentInstallError("npm_lock_non_registry_source")
        resolved = package.get("resolved")
        if resolved is not None and (
            not isinstance(resolved, str) or not resolved.startswith("https://")
        ):
            raise AgentInstallError("npm_lock_non_registry_source")
    canonical = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()
    try:
        DistributionResolutionV1(
            distribution_digest="0" * 64,
            artifact_digest="0" * 64,
            package_integrity=integrity,
        )
    except ValueError as error:
        raise AgentInstallError("npm_integrity_invalid") from error
    return canonical, integrity


def _validate_json_bounds(value: object) -> None:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NPM_LOCK_NODES or depth > MAX_NPM_LOCK_DEPTH:
            raise AgentInstallError("npm_lock_too_complex")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 2_048:
                    raise AgentInstallError("npm_lock_invalid")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _resolve_npm_entrypoint(payload: Path, resolution: NpxPackageResolution) -> str:
    package_root = payload / "node_modules" / resolution.package_name
    metadata = read_json(package_root / "package.json")
    if (
        metadata.get("name") != resolution.package_name
        or metadata.get("version") != resolution.version
    ):
        raise AgentInstallError("npm_installed_package_mismatch")
    bin_value = metadata.get("bin")
    preferred = resolution.package_name.rsplit("/", maxsplit=1)[-1]
    if isinstance(bin_value, str):
        entrypoint = preferred
    elif isinstance(bin_value, dict) and all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in bin_value.items()
    ):
        if preferred in bin_value:
            entrypoint = preferred
        elif len(bin_value) == 1:
            entrypoint = next(iter(bin_value))
        else:
            raise AgentInstallError("npm_entrypoint_ambiguous")
    else:
        raise AgentInstallError("npm_entrypoint_missing")
    bin_root = payload / "node_modules" / ".bin"
    candidates = (bin_root / entrypoint, bin_root / f"{entrypoint}.cmd")
    shim = next((item for item in candidates if item.exists()), None)
    if shim is None:
        raise AgentInstallError("npm_entrypoint_missing")
    try:
        resolved = shim.resolve(strict=True)
    except OSError as error:
        raise AgentInstallError("npm_entrypoint_invalid") from error
    if not resolved.is_relative_to(payload.resolve()) or not resolved.is_file():
        raise AgentInstallError("npm_entrypoint_outside_managed_root")
    return shim.relative_to(payload).as_posix()


def _write_package_json(path: Path, package_name: str, version: str) -> None:
    atomic_write_json(
        path,
        {
            "name": "gigaloom-managed-acp-agent",
            "private": True,
            "version": "0.0.0",
            "dependencies": {package_name: version},
        },
    )


def _npm_environment(npm: Path, home: Path, cache: Path) -> tuple[tuple[str, str], ...]:
    ensure_private_directory(home)
    ensure_private_directory(cache)
    user_config = home / ".npmrc"
    user_config.touch(mode=0o600, exist_ok=True)
    return private_command_environment(
        npm,
        home,
        {
            "npm_config_audit": "false",
            "npm_config_cache": str(cache),
            "npm_config_fund": "false",
            "npm_config_global": "false",
            "npm_config_update_notifier": "false",
            "npm_config_userconfig": str(user_config),
        },
    )


def _exact_package(value: str) -> tuple[str, str]:
    match = _EXACT_NPX_RE.fullmatch(value)
    if match is None:
        raise AgentInstallError("npx_package_not_exact")
    return match.group("name"), match.group("version")


def _absolute_executable(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} executable must be an absolute file")
    return path


def _read_bounded(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if path.is_symlink() or not 0 < size <= MAX_PACKAGE_LOCK_BYTES:
            raise AgentInstallError("npm_lock_invalid")
        return path.read_bytes()
    except OSError as error:
        raise AgentInstallError("npm_lock_unavailable") from error
