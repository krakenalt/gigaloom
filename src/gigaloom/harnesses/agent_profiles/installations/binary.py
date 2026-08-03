"""Transactional installation of bounded ACP binary distributions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import shutil

from gigaloom.contracts import (
    ACPDistributionKind,
    AgentCleanupStatus,
    AgentInstallPlanV1,
    AgentIntegrityPolicy,
    ExtractionLimitsV1,
    InstallationTransitionV1,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
)
from gigaloom.contracts.agent_installation_codec import managed_agent_artifact_to_dict
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.archives import (
    extract_binary_archive,
)
from gigaloom.harnesses.agent_profiles.installations.errors import (
    AgentInstallCancelled,
    AgentInstallError,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
    require_within,
)
from gigaloom.harnesses.agent_profiles.installations.journal import (
    InstallCancellationToken,
    InstallationJournal,
)
from gigaloom.harnesses.agent_profiles.installations.transport import (
    DEFAULT_BINARY_TIMEOUT_SECONDS,
    BinaryDownloadRequest,
    BinaryDownloadTransport,
    is_binary_download_url_allowed,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


DEFAULT_BINARY_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_BINARY_EXTRACTION_LIMITS = ExtractionLimitsV1(
    max_bytes=512 * 1024 * 1024,
    max_files=10_000,
    max_path_depth=32,
)


@dataclass(frozen=True, slots=True)
class BinaryInstallResult:
    """Installed-but-inactive artifact and content-free transaction evidence."""

    artifact: ManagedAgentArtifactV1
    transitions: tuple[InstallationTransitionV1, ...]
    bytes_received: int
    artifact_digest: str
    unverified_source: bool
    journal_digest: str


@dataclass(frozen=True, slots=True)
class StagingRecoveryResult:
    """Bounded restart cleanup result for one owned abandoned transaction."""

    plan_id: str
    cleanup_status: AgentCleanupStatus


class BinaryAgentInstaller:
    """Download, validate, and atomically publish one binary plan."""

    def __init__(
        self,
        data_root: str | Path,
        transport: BinaryDownloadTransport,
        *,
        clock: Callable[[], datetime] | None = None,
        max_download_bytes: int = DEFAULT_BINARY_MAX_DOWNLOAD_BYTES,
        extraction_limits: ExtractionLimitsV1 = DEFAULT_BINARY_EXTRACTION_LIMITS,
        timeout_seconds: float = DEFAULT_BINARY_TIMEOUT_SECONDS,
    ) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_download_bytes = max_download_bytes
        self._limits = extraction_limits
        self._timeout_seconds = timeout_seconds
        if max_download_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("binary installer bounds are invalid")

    @property
    def staging_root(self) -> Path:
        """Return the sole owned restart-recovery root."""
        return self._data_root / "agents" / "staging"

    def install(
        self,
        plan: AgentInstallPlanV1,
        *,
        confirmed: bool,
        allow_unverified: bool = False,
        cancellation: InstallCancellationToken | None = None,
    ) -> BinaryInstallResult:
        """Execute one confirmed plan without activating its generated route."""
        self._validate_plan(
            plan, confirmed=confirmed, allow_unverified=allow_unverified
        )
        token = cancellation or InstallCancellationToken()
        staging = require_within(
            Path(plan.staging_root),
            self.staging_root,
            reason_code="binary_staging_root_outside_authority",
        )
        if staging.parent != self.staging_root or staging.name != plan.plan_id:
            raise AgentInstallError("binary_staging_root_not_plan_scoped")
        ensure_private_directory(self._data_root)
        ensure_private_directory(self.staging_root)
        lock_path = self._data_root / "agents" / ".install.lock"
        with registry_cache_lock(lock_path):
            if staging.exists():
                self._cleanup(staging)
            ensure_private_directory(staging)
            journal = InstallationJournal(plan.plan_id, staging / "journal.json")
            try:
                return self._install_locked(plan, staging, journal, token)
            except AgentInstallError as error:
                cleanup = self._cleanup(staging)
                error.transitions = journal.transitions
                error.cleanup_status = cleanup
                raise
            except Exception as error:
                cleanup = self._cleanup(staging)
                raise AgentInstallError(
                    "binary_install_internal_failure",
                    transitions=journal.transitions,
                    cleanup_status=cleanup,
                ) from error

    def recover_abandoned(self) -> tuple[StagingRecoveryResult, ...]:
        """Remove only owned staging directories carrying a strict journal marker."""
        if not self.staging_root.exists():
            return ()
        require_within(
            self.staging_root / "sentinel",
            self.staging_root,
            reason_code="binary_staging_root_outside_authority",
        )
        results = []
        for path in sorted(self.staging_root.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_dir():
                continue
            journal_path = path / "journal.json"
            try:
                value = read_json(journal_path)
            except AgentInstallError:
                continue
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != 1
                or value.get("content_free") is not True
                or not isinstance(value.get("plan_id"), str)
                or path.name != value["plan_id"]
            ):
                continue
            results.append(
                StagingRecoveryResult(
                    plan_id=path.name,
                    cleanup_status=self._cleanup(path),
                )
            )
        return tuple(results)

    def _install_locked(
        self,
        plan: AgentInstallPlanV1,
        staging: Path,
        journal: InstallationJournal,
        token: InstallCancellationToken,
    ) -> BinaryInstallResult:
        self._checkpoint(token, journal, "planned", plan.plan_id)
        response = self._transport.fetch(
            BinaryDownloadRequest(
                url=plan.source,
                timeout_seconds=self._timeout_seconds,
                max_bytes=self._max_download_bytes,
                allowed_origins=plan.network_origins,
            )
        )
        if response.status_code != 200 or not is_binary_download_url_allowed(
            response.final_url,
            plan.network_origins,
        ):
            raise AgentInstallError("binary_download_response_invalid")
        if response.content_length is not None and (
            response.content_length < 0
            or response.content_length > self._max_download_bytes
        ):
            raise AgentInstallError("binary_download_too_large")
        self._checkpoint(token, journal, "downloading", plan.source)
        archive_path = staging / "archive.part"
        received, artifact_digest = self._write_download(
            response.chunks, archive_path, token
        )
        if response.content_length is not None and received != response.content_length:
            raise AgentInstallError("binary_content_length_mismatch")
        if (
            plan.expected_integrity is not None
            and plan.expected_integrity.removeprefix("sha256:") != artifact_digest
        ):
            raise AgentInstallError("binary_digest_mismatch")
        self._checkpoint(token, journal, "downloaded", artifact_digest)
        payload_root = staging / "payload"
        executable = extract_binary_archive(
            archive_path,
            payload_root,
            archive_name=plan.package_or_archive,
            command=plan.command,
            limits=self._limits,
        )
        self._checkpoint(token, journal, "extracted", artifact_digest)
        managed_root = self._final_root(plan, artifact_digest)
        install_id = (
            "install-"
            + canonical_digest(
                {
                    "registry_id": plan.registry_id,
                    "local_agent_id": plan.local_agent_id,
                    "version": plan.version,
                    "platform": plan.platform,
                    "architecture": plan.architecture,
                    "artifact_digest": artifact_digest,
                }
            )[:24]
        )
        installed_at = self._now()
        artifact = ManagedAgentArtifactV1(
            install_id=install_id,
            registry_id=plan.registry_id,
            local_agent_id=plan.local_agent_id,
            version=plan.version,
            distribution_kind=plan.distribution_kind,
            platform=plan.platform,
            artifact_digest=artifact_digest,
            package_integrity=plan.expected_integrity,
            lock_digest=canonical_digest(
                {
                    "entry_digest": plan.entry_digest,
                    "snapshot_digest": plan.snapshot_digest,
                    "distribution_kind": plan.distribution_kind.value,
                    "artifact_digest": artifact_digest,
                }
            ),
            managed_root=str(managed_root),
            executable_relative_path=executable,
            command=plan.command,
            arguments=plan.arguments,
            environment=plan.environment,
            installed_at=installed_at,
            status=ManagedAgentStatus.STAGED,
        )
        atomic_write_json(
            payload_root / ".artifact.json", managed_agent_artifact_to_dict(artifact)
        )
        self._checkpoint(token, journal, "publishing", artifact_digest)
        ensure_private_directory(managed_root.parent)
        if managed_root.exists():
            raise AgentInstallError("managed_artifact_already_exists")
        os.replace(payload_root, managed_root)
        try:
            archive_path.unlink(missing_ok=True)
            journal.append(
                "installed_inactive",
                reason_code="binary_artifact_published",
                evidence={"install_id": install_id, "artifact_digest": artifact_digest},
                timestamp=self._now(),
            )
        except Exception:
            self._cleanup(managed_root)
            raise
        journal_digest = canonical_digest(
            [item.evidence_digest for item in journal.transitions]
        )
        self._cleanup(staging)
        return BinaryInstallResult(
            artifact=artifact,
            transitions=journal.transitions,
            bytes_received=received,
            artifact_digest=artifact_digest,
            unverified_source=plan.expected_integrity is None,
            journal_digest=journal_digest,
        )

    def _write_download(
        self,
        chunks,
        path: Path,
        token: InstallCancellationToken,
    ) -> tuple[int, str]:  # noqa: ANN001
        received = 0
        digest = hashlib.sha256()
        try:
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                for chunk in chunks:
                    if token.cancelled:
                        raise AgentInstallCancelled("binary_install_cancelled")
                    if not isinstance(chunk, bytes) or not chunk:
                        raise AgentInstallError("binary_download_chunk_invalid")
                    received += len(chunk)
                    if received > self._max_download_bytes:
                        raise AgentInstallError("binary_download_too_large")
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except AgentInstallError:
            raise
        except OSError as error:
            raise AgentInstallError("binary_download_write_failed") from error
        if received == 0:
            raise AgentInstallError("binary_download_empty")
        return received, digest.hexdigest()

    def _final_root(self, plan: AgentInstallPlanV1, artifact_digest: str) -> Path:
        proposed = Path(plan.managed_root)
        if plan.expected_integrity is None:
            proposed = proposed.parent / artifact_digest
        root = self._data_root / "agents" / "registry"
        return require_within(
            proposed,
            root,
            reason_code="binary_managed_root_outside_authority",
        )

    def _checkpoint(
        self,
        token: InstallCancellationToken,
        journal: InstallationJournal,
        state: str,
        evidence: object,
    ) -> None:
        if token.cancelled:
            raise AgentInstallCancelled("binary_install_cancelled")
        journal.append(
            state,
            reason_code=f"binary_{state}",
            evidence=evidence,
            timestamp=self._now(),
        )

    def _validate_plan(
        self,
        plan: AgentInstallPlanV1,
        *,
        confirmed: bool,
        allow_unverified: bool,
    ) -> None:
        if (
            not isinstance(plan, AgentInstallPlanV1)
            or plan.distribution_kind is not ACPDistributionKind.BINARY
        ):
            raise AgentInstallError("binary_plan_required")
        if plan.confirmation_required and not confirmed:
            raise AgentInstallError("binary_confirmation_required")
        if plan.expires_at < self._now():
            raise AgentInstallError("binary_plan_expired")
        unverified = (
            plan.integrity_policy is AgentIntegrityPolicy.ALLOW_EXPLICIT_UNVERIFIED
        )
        if unverified != (plan.expected_integrity is None):
            raise AgentInstallError("binary_integrity_policy_inconsistent")
        if unverified and not allow_unverified:
            raise AgentInstallError("binary_unverified_confirmation_required")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("binary installer clock must be timezone-aware")
        return value

    @staticmethod
    def _cleanup(path: Path) -> AgentCleanupStatus:
        try:
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=False)
            return AgentCleanupStatus.COMPLETED
        except FileNotFoundError:
            return AgentCleanupStatus.COMPLETED
        except OSError:
            return AgentCleanupStatus.INCOMPLETE
