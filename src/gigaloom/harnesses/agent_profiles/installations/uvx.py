"""Exact uv resolution and private Python ACP agent environments."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
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
    require_within,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionResolutionV1,
)
from gigaloom.harnesses.agent_profiles.installations.packages import (
    MAX_PACKAGE_LOCK_BYTES,
    PackageInstallResult,
    UvxPackageResolution,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


_EXACT_UVX_RE = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9,._-]+\])?=="
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\Z"
)
_LOCK_REQUIREMENT_RE = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)=="
    r"(?P<version>[^\s;\\]+)(?:\s*;[^\\]+)?(?P<rest>.*)\Z"
)
_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}\b")
MAX_INTERPRETER_BYTES = 512 * 1024 * 1024


class UvxPackageResolver:
    """Compile an exact hash-locked requirement against one interpreter."""

    def __init__(
        self,
        data_root: str | Path,
        uv_executable: str | Path,
        runner: PackageCommandRunner,
    ) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._uv = _absolute_executable(uv_executable, "uv")
        self._runner = runner

    def resolve(
        self,
        distribution: ACPDistributionV1,
        *,
        interpreter: str | Path,
    ) -> UvxPackageResolution:
        """Resolve to a private canonical hashed lock without installing a tool."""
        if distribution.kind is not ACPDistributionKind.UVX:
            raise AgentInstallError("uvx_distribution_required")
        package_name, version = _exact_package(distribution.package_or_archive)
        interpreter_path = _absolute_executable(interpreter, "Python")
        fingerprint = interpreter_fingerprint(interpreter_path)
        root = self._data_root / "agents" / "resolution" / "uv"
        ensure_private_directory(root)
        working = root / f"resolve-{uuid4().hex}"
        ensure_private_directory(working)
        try:
            input_path = working / "requirements.in"
            output_path = working / "requirements.lock"
            input_path.write_text(
                distribution.package_or_archive + "\n", encoding="utf-8"
            )
            input_path.chmod(0o600)
            home = working / "home"
            ensure_private_directory(home)
            command = PackageCommand(
                argv=(
                    str(self._uv),
                    "pip",
                    "compile",
                    "--no-config",
                    "--no-sources",
                    "--generate-hashes",
                    "--no-header",
                    "--no-annotate",
                    "--python",
                    str(interpreter_path),
                    "--output-file",
                    str(output_path),
                    str(input_path),
                ),
                cwd=str(working),
                environment=_uv_environment(self._uv, home, working / "cache"),
            )
            result = self._runner.run(command)
            if result.return_code != 0:
                raise AgentInstallError("uv_resolution_failed")
            lock = _validate_uv_lock(
                _read_bounded(output_path),
                package_name,
                version,
            )
            lock_digest = hashlib.sha256(lock).hexdigest()
            return UvxPackageResolution(
                evidence=DistributionResolutionV1(
                    distribution_digest=distribution.distribution_digest,
                    artifact_digest=lock_digest,
                    lock_digest=lock_digest,
                    interpreter_fingerprint=fingerprint,
                ),
                package=distribution.package_or_archive,
                package_name=package_name,
                version=version,
                lock_bytes=lock,
                interpreter=str(interpreter_path),
            )
        finally:
            shutil.rmtree(working, ignore_errors=True)


class UvxAgentInstaller:
    """Synchronize a hash-locked package into one private uv environment."""

    def __init__(
        self,
        data_root: str | Path,
        uv_executable: str | Path,
        runner: PackageCommandRunner,
        *,
        clock=None,  # noqa: ANN001
    ) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._uv = _absolute_executable(uv_executable, "uv")
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def install(
        self,
        plan: AgentInstallPlanV1,
        resolution: UvxPackageResolution,
        *,
        confirmed: bool,
    ) -> PackageInstallResult:
        """Create and publish one exact lock-bound private Python environment."""
        self._validate_plan(plan, resolution, confirmed=confirmed)
        staging = require_within(
            Path(plan.staging_root),
            self._data_root / "agents" / "staging",
            reason_code="uvx_staging_root_outside_authority",
        )
        if staging.name != plan.plan_id:
            raise AgentInstallError("uvx_staging_root_not_plan_scoped")
        managed = require_within(
            Path(plan.managed_root),
            self._data_root / "agents" / "registry",
            reason_code="uvx_managed_root_outside_authority",
        )
        ensure_private_directory(self._data_root / "agents" / "staging")
        with registry_cache_lock(self._data_root / "agents" / ".install.lock"):
            if staging.exists():
                shutil.rmtree(staging)
            ensure_private_directory(staging)
            payload = staging / "payload"
            ensure_private_directory(payload)
            try:
                return self._install_locked(plan, resolution, payload, managed)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def _install_locked(
        self,
        plan: AgentInstallPlanV1,
        resolution: UvxPackageResolution,
        payload: Path,
        managed: Path,
    ) -> PackageInstallResult:
        lock_path = payload / "requirements.lock"
        lock_path.write_bytes(resolution.lock_bytes)
        lock_path.chmod(0o600)
        home = payload / ".home"
        cache = payload / ".uv-cache"
        ensure_private_directory(home)
        environment = _uv_environment(self._uv, home, cache)
        environment_root = payload / "environment"
        create = PackageCommand(
            argv=(
                str(self._uv),
                "venv",
                "--no-config",
                "--python",
                resolution.interpreter,
                "--no-python-downloads",
                str(environment_root),
            ),
            cwd=str(payload),
            environment=environment,
        )
        create_result = self._runner.run(create)
        if create_result.return_code != 0:
            raise AgentInstallError("uv_environment_creation_failed")
        python = _environment_python(environment_root)
        sync = PackageCommand(
            argv=(
                str(self._uv),
                "pip",
                "sync",
                "--no-config",
                "--no-sources",
                "--require-hashes",
                "--python",
                str(python),
                str(lock_path),
            ),
            cwd=str(payload),
            environment=environment,
        )
        sync_result = self._runner.run(sync)
        if sync_result.return_code != 0:
            raise AgentInstallError("uv_environment_sync_failed")
        if (
            interpreter_fingerprint(resolution.interpreter)
            != resolution.evidence.interpreter_fingerprint
        ):
            raise AgentInstallError("uv_interpreter_fingerprint_mismatch")
        if (
            _validate_uv_lock(
                _read_bounded(lock_path), resolution.package_name, resolution.version
            )
            != resolution.lock_bytes
        ):
            raise AgentInstallError("uv_lock_mismatch")
        executable = _resolve_uv_entrypoint(environment_root, resolution.package_name)
        install_id = (
            "install-"
            + canonical_digest(
                {
                    "registry_id": plan.registry_id,
                    "local_agent_id": plan.local_agent_id,
                    "version": plan.version,
                    "platform": plan.platform,
                    "architecture": plan.architecture,
                    "lock_digest": resolution.evidence.lock_digest,
                    "interpreter_fingerprint": resolution.evidence.interpreter_fingerprint,
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
            package_integrity=None,
            lock_digest=cast(str, resolution.evidence.lock_digest),
            managed_root=str(managed),
            executable_relative_path=(Path("environment") / executable).as_posix(),
            command=Path(executable).name,
            arguments=plan.arguments,
            environment=plan.environment,
            installed_at=self._now(),
            status=ManagedAgentStatus.STAGED,
        )
        atomic_write_json(
            payload / ".uv-resolution.json",
            {
                "schema_version": 1,
                "lock_digest": resolution.evidence.lock_digest,
                "interpreter_fingerprint": resolution.evidence.interpreter_fingerprint,
                "package": resolution.package,
            },
        )
        atomic_write_json(
            payload / ".artifact.json", managed_agent_artifact_to_dict(artifact)
        )
        evidence_digest = canonical_digest(
            {
                "create_argv": list(create.argv),
                "sync_argv": list(sync.argv),
                "lock_digest": resolution.evidence.lock_digest,
                "create_stdout_digest": hashlib.sha256(
                    create_result.stdout
                ).hexdigest(),
                "create_stderr_digest": hashlib.sha256(
                    create_result.stderr
                ).hexdigest(),
                "sync_stdout_digest": hashlib.sha256(sync_result.stdout).hexdigest(),
                "sync_stderr_digest": hashlib.sha256(sync_result.stderr).hexdigest(),
            }
        )
        result = PackageInstallResult(
            artifact=artifact,
            lock_digest=cast(str, resolution.evidence.lock_digest),
            package_integrity=None,
            command_evidence_digest=evidence_digest,
        )
        ensure_private_directory(managed.parent)
        if managed.exists():
            raise AgentInstallError("managed_artifact_already_exists")
        os.replace(payload, managed)
        return result

    def _validate_plan(
        self,
        plan: AgentInstallPlanV1,
        resolution: UvxPackageResolution,
        *,
        confirmed: bool,
    ) -> None:
        if plan.distribution_kind is not ACPDistributionKind.UVX:
            raise AgentInstallError("uvx_plan_required")
        if plan.confirmation_required and not confirmed:
            raise AgentInstallError("uvx_confirmation_required")
        if plan.expires_at < self._now():
            raise AgentInstallError("uvx_plan_expired")
        if (
            plan.package_or_archive != resolution.package
            or plan.expected_integrity != resolution.evidence.artifact_digest
            or not plan.managed_root.endswith(resolution.evidence.artifact_digest)
        ):
            raise AgentInstallError("uvx_resolution_not_bound_to_plan")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("uvx installer clock must be timezone-aware")
        return value


def interpreter_fingerprint(value: str | Path) -> str:
    """Hash one explicit interpreter file without executing project code."""
    path = _absolute_executable(value, "Python").resolve(strict=True)
    try:
        size = path.stat().st_size
        if not 0 < size <= MAX_INTERPRETER_BYTES:
            raise AgentInstallError("uv_interpreter_size_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise AgentInstallError("uv_interpreter_unavailable") from error
    return canonical_digest({"sha256": digest.hexdigest(), "size": size})


def _validate_uv_lock(raw: bytes, package_name: str, version: str) -> bytes:
    if not raw or len(raw) > MAX_PACKAGE_LOCK_BYTES:
        raise AgentInstallError("uv_lock_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AgentInstallError("uv_lock_invalid") from error
    if "\r" in text or any(
        token in text for token in ("git+", "file:", "-e ", "--index-url", " @ http")
    ):
        raise AgentInstallError("uv_lock_non_registry_source")
    logical = text.replace("\\\n", " ")
    requirements: dict[str, str] = {}
    for raw_line in logical.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCK_REQUIREMENT_RE.fullmatch(line)
        if match is None or _HASH_RE.search(line) is None:
            raise AgentInstallError("uv_lock_requirement_not_exact_or_hashed")
        name = _normalize_name(match.group("name"))
        if name in requirements:
            raise AgentInstallError("uv_lock_duplicate_requirement")
        requirements[name] = match.group("version")
    if requirements.get(_normalize_name(package_name)) != version:
        raise AgentInstallError("uv_lock_target_not_exact")
    return (
        "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"
    ).encode()


def _resolve_uv_entrypoint(environment_root: Path, package_name: str) -> str:
    bin_root = environment_root / ("Scripts" if os.name == "nt" else "bin")
    preferred = {_normalize_name(package_name), package_name.replace("-", "_")}
    candidates = [
        path
        for path in bin_root.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and path.name.lower()
        not in {"python", "python3", "python.exe", "pip", "pip3", "activate"}
    ]
    selected = next(
        (path for path in candidates if path.stem.lower() in preferred),
        None,
    )
    if selected is None and len(candidates) == 1:
        selected = candidates[0]
    if selected is None:
        raise AgentInstallError("uv_entrypoint_missing_or_ambiguous")
    selected.chmod(0o700)
    return selected.relative_to(environment_root).as_posix()


def _environment_python(root: Path) -> Path:
    candidates = (
        root / "Scripts" / "python.exe",
        root / "bin" / "python",
    )
    value = next((path for path in candidates if path.is_file()), None)
    if value is None:
        raise AgentInstallError("uv_environment_python_missing")
    return value


def _uv_environment(uv: Path, home: Path, cache: Path) -> tuple[tuple[str, str], ...]:
    ensure_private_directory(home)
    ensure_private_directory(cache)
    ensure_private_directory(home / ".local/bin")
    ensure_private_directory(home / ".uv-tools")
    return private_command_environment(
        uv,
        home,
        {
            "UV_CACHE_DIR": str(cache),
            "UV_NO_CONFIG": "1",
            "UV_NO_SOURCES": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_TOOL_BIN_DIR": str(home / ".local/bin"),
            "UV_TOOL_DIR": str(home / ".uv-tools"),
        },
    )


def _exact_package(value: str) -> tuple[str, str]:
    match = _EXACT_UVX_RE.fullmatch(value)
    if match is None:
        raise AgentInstallError("uvx_package_not_exact")
    return match.group("name"), match.group("version")


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _absolute_executable(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} executable must be an absolute file")
    return path


def _read_bounded(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if path.is_symlink() or not 0 < size <= MAX_PACKAGE_LOCK_BYTES:
            raise AgentInstallError("uv_lock_invalid")
        return path.read_bytes()
    except OSError as error:
        raise AgentInstallError("uv_lock_unavailable") from error
