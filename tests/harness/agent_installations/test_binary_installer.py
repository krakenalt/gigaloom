"""Hermetic transactional binary installation and rollback coverage."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from gigaloom.contracts import AgentActivationStatus, AgentCleanupStatus
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentInstallCancelled,
    AgentInstallError,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
    BinaryAgentInstaller,
    BinaryDownloadResponse,
    InstallCancellationToken,
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.registry import decode_registry_document


NOW = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)


class MemoryTransport:
    """Deterministic in-memory response owner with no network authority."""

    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str,
        status_code: int = 200,
        chunks: tuple[bytes, ...] | None = None,
    ) -> None:
        self.payload = payload
        self.final_url = final_url
        self.status_code = status_code
        self.chunks = chunks or (payload,)
        self.requests = []

    def fetch(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        return BinaryDownloadResponse(
            status_code=self.status_code,
            final_url=self.final_url,
            content_length=len(self.payload),
            chunks=self.chunks,
        )


class FailedTransport:
    def fetch(self, request):  # noqa: ANN001, ANN201, ARG002
        raise AgentInstallError("binary_download_failed")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _catalog(
    archive: bytes,
    *,
    registry_id: str = "generic-binary",
    version: str = "1.0.0",
    include_digest: bool = True,
    command: str = "bin/generic-agent",
):
    target = {
        "archive": f"https://downloads.example.test/{registry_id}-{version}.zip",
        "cmd": command,
        "args": ["--acp"],
    }
    if include_digest:
        target["sha256"] = hashlib.sha256(archive).hexdigest()
    payload = json.dumps(
        {
            "version": "1.0.0",
            "agents": [
                {
                    "id": registry_id,
                    "name": "Generic binary agent",
                    "version": version,
                    "description": "Hermetic binary fixture",
                    "distribution": {"binary": {"darwin-aarch64": target}},
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    return decode_registry_document(payload, fetched_at=NOW)


def _plan(tmp_path: Path, catalog, *, local_agent_id: str | None = None):  # noqa: ANN001
    planner = AgentInstallPlanner(
        AgentInstallPlannerPolicy(
            platform="darwin",
            architecture="aarch64",
            data_root=str(tmp_path),
        )
    )
    result = planner.plan(
        catalog.entries[0],
        catalog.snapshot,
        inventory=AgentIdentityInventory(),
        local_agent_id=local_agent_id,
        now=NOW,
    )
    assert result.plan is not None
    return result.plan


def test_verified_zip_installs_inactive_under_immutable_digest_root(tmp_path):
    archive = _zip_bytes({"bin/generic-agent": b"#!/bin/sh\nexit 0\n"})
    catalog = _catalog(archive)
    plan = _plan(tmp_path, catalog)
    transport = MemoryTransport(archive, final_url=plan.source)

    result = BinaryAgentInstaller(
        tmp_path,
        transport,
        clock=lambda: NOW,
    ).install(plan, confirmed=True)

    executable = Path(result.artifact.managed_root) / "bin/generic-agent"
    assert executable.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert executable.stat().st_mode & 0o777 == 0o700
    assert (
        Path(result.artifact.managed_root).name == hashlib.sha256(archive).hexdigest()
    )
    assert (Path(result.artifact.managed_root) / ".artifact.json").is_file()
    assert not Path(plan.staging_root).exists()
    assert result.bytes_received == len(archive)
    assert result.unverified_source is False
    assert result.transitions[-1].state == "installed_inactive"
    assert len(transport.requests) == 1


def test_unverified_binary_requires_a_distinct_explicit_admission(tmp_path):
    archive = _zip_bytes({"bin/generic-agent": b"binary"})
    catalog = _catalog(archive, include_digest=False)
    plan = _plan(tmp_path, catalog)
    transport = MemoryTransport(archive, final_url=plan.source)
    installer = BinaryAgentInstaller(tmp_path, transport, clock=lambda: NOW)

    with pytest.raises(AgentInstallError, match="unverified_confirmation"):
        installer.install(plan, confirmed=True)
    result = installer.install(plan, confirmed=True, allow_unverified=True)

    assert result.unverified_source is True
    assert Path(result.artifact.managed_root).name == result.artifact_digest
    assert result.artifact.package_integrity is None


@pytest.mark.parametrize(
    ("transport_factory", "reason"),
    [
        (lambda plan, archive: FailedTransport(), "binary_download_failed"),
        (
            lambda plan, archive: MemoryTransport(
                archive + b"tampered",
                final_url=plan.source,
            ),
            "binary_digest_mismatch",
        ),
    ],
)
def test_download_failure_and_digest_mismatch_clean_staging(
    tmp_path,
    transport_factory,
    reason,
):
    archive = _zip_bytes({"bin/generic-agent": b"binary"})
    catalog = _catalog(archive)
    plan = _plan(tmp_path, catalog)
    installer = BinaryAgentInstaller(
        tmp_path,
        transport_factory(plan, archive),
        clock=lambda: NOW,
    )

    with pytest.raises(AgentInstallError, match=reason) as captured:
        installer.install(plan, confirmed=True)

    assert captured.value.cleanup_status is AgentCleanupStatus.COMPLETED
    assert not Path(plan.staging_root).exists()
    assert not Path(plan.managed_root).exists()


def test_archive_traversal_is_rejected_without_writing_outside_staging(tmp_path):
    archive = _zip_bytes(
        {
            "bin/generic-agent": b"binary",
            "../escaped": b"must-not-write",
        }
    )
    catalog = _catalog(archive)
    plan = _plan(tmp_path, catalog)

    with pytest.raises(AgentInstallError, match="unsafe_path") as captured:
        BinaryAgentInstaller(
            tmp_path,
            MemoryTransport(archive, final_url=plan.source),
            clock=lambda: NOW,
        ).install(plan, confirmed=True)

    assert captured.value.cleanup_status is AgentCleanupStatus.COMPLETED
    assert not (tmp_path / "agents" / "escaped").exists()
    assert not (tmp_path / "escaped").exists()


def test_pre_requested_cancellation_cleans_owned_staging(tmp_path):
    archive = _zip_bytes({"bin/generic-agent": b"binary"})
    catalog = _catalog(archive)
    plan = _plan(tmp_path, catalog)
    token = InstallCancellationToken()
    token.cancel()

    with pytest.raises(AgentInstallCancelled) as captured:
        BinaryAgentInstaller(
            tmp_path,
            MemoryTransport(archive, final_url=plan.source),
            clock=lambda: NOW,
        ).install(plan, confirmed=True, cancellation=token)

    assert captured.value.cleanup_status is AgentCleanupStatus.COMPLETED
    assert not Path(plan.staging_root).exists()


def test_restart_recovery_removes_only_strictly_owned_journals(tmp_path):
    staging = tmp_path / "agents/staging"
    abandoned = staging / "plan-abandoned"
    abandoned.mkdir(parents=True)
    (abandoned / "journal.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_id": "plan-abandoned",
                "transitions": [],
                "content_free": True,
            }
        ),
        encoding="utf-8",
    )
    unowned = staging / "user-directory"
    unowned.mkdir()
    (unowned / "note.txt").write_text("preserve", encoding="utf-8")
    installer = BinaryAgentInstaller(tmp_path, FailedTransport(), clock=lambda: NOW)

    result = installer.recover_abandoned()

    assert result[0].plan_id == "plan-abandoned"
    assert result[0].cleanup_status is AgentCleanupStatus.COMPLETED
    assert not abandoned.exists()
    assert (unowned / "note.txt").read_text(encoding="utf-8") == "preserve"


def test_activation_pointer_retains_previous_and_rolls_back_atomically(tmp_path):
    first_archive = _zip_bytes({"bin/generic-agent": b"version-one"})
    second_archive = _zip_bytes({"bin/generic-agent": b"version-two"})
    first_catalog = _catalog(first_archive, version="1.0.0")
    second_catalog = _catalog(second_archive, version="2.0.0")
    first_plan = _plan(tmp_path, first_catalog)
    second_plan = _plan(tmp_path, second_catalog)
    first = BinaryAgentInstaller(
        tmp_path,
        MemoryTransport(first_archive, final_url=first_plan.source),
        clock=lambda: NOW,
    ).install(first_plan, confirmed=True)
    second = BinaryAgentInstaller(
        tmp_path,
        MemoryTransport(second_archive, final_url=second_plan.source),
        clock=lambda: NOW,
    ).install(second_plan, confirmed=True)
    store = ManagedAgentActivationStore(tmp_path)

    store.activate(
        first.artifact,
        profile_digest="a" * 64,
        compatibility_observation_digest="b" * 64,
        status=AgentActivationStatus.READY,
        activated_at=NOW,
    )
    second_activation = store.activate(
        second.artifact,
        profile_digest="c" * 64,
        compatibility_observation_digest="d" * 64,
        status=AgentActivationStatus.READY,
        activated_at=NOW,
    )
    rollback = store.rollback(
        first.artifact.local_agent_id,
        profile_digest="a" * 64,
        compatibility_observation_digest="b" * 64,
        activated_at=NOW,
    )

    assert second_activation.previous_install_id == first.artifact.install_id
    assert rollback.install_id == first.artifact.install_id
    assert rollback.previous_install_id == second.artifact.install_id
    current = store.current(first.artifact.local_agent_id)
    assert current is not None and current[0].install_id == first.artifact.install_id
