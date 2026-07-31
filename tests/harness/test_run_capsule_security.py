from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from gigaloom.review.capsules import (
    CapsuleArchiveError,
    CapsuleCheckoutError,
    CapsuleIntegrityError,
    CapsuleSchemaError,
    Ed25519Signer,
    FindingStatus,
    VerificationStatus,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    canonical_json_bytes,
    capture_run_capsule,
    export_run_capsule,
    verify_run_capsule,
)

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"
)


def _fixture() -> dict[str, object]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _bundle(*, signed: bool, input_payload: dict[str, object] | None = None):
    fixture = _fixture()
    signer = (
        Ed25519Signer.from_private_bytes(
            b"\x11" * 32,
            signer_id="security-fixture",
            trust_status="test-only",
            key_rotation_id="fixture-v1",
        )
        if signed
        else None
    )
    return capture_run_capsule(
        input_lock=build_input_lock(input_payload or fixture["input_lock"]),
        output_receipt=build_output_receipt(fixture["output_receipt"]),
        artifacts=build_artifact_manifest(fixture["artifacts"]),
        omissions=build_omission_manifest(fixture["omissions"]),
        created_at=str(fixture["created_at"]),
        signer=signer,
    )


def _rewrite_archive(
    source: Path,
    destination: Path,
    mutate,
) -> Path:
    with zipfile.ZipFile(source) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for info, data in members:
            archive.writestr(info, mutate(info.filename, data))
    return destination


def test_committed_standalone_fixture_exports_and_verifies_offline(tmp_path) -> None:
    archive = export_run_capsule(_bundle(signed=False), tmp_path / "fixture.zip")
    report = verify_run_capsule(
        archive,
        observed_inputs={
            "agent_profile.sha256": "a" * 64,
            "acp.capabilities_sha256": "d" * 64,
            "environment.toolchain": "c" * 64,
        },
    )
    assert report.status is VerificationStatus.VERIFIED
    assert {finding.status for finding in report.findings} == {FindingStatus.MATCHED}
    serialized = archive.read_bytes()
    for forbidden in (b"prompt", b"terminal_transcript", b"tool_output", b"secret"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("member_suffix", "mutate", "error", "message"),
    [
        (
            "artifacts.json",
            lambda data: canonical_json_bytes(
                {**json.loads(data), "artifacts_sha256": "f" * 64}
            ),
            CapsuleIntegrityError,
            "artifact manifest digest",
        ),
        (
            "capsule.json",
            lambda data: canonical_json_bytes(
                {**json.loads(data), "schema_version": 2}
            ),
            CapsuleSchemaError,
            "unsupported run capsule schema_version",
        ),
        (
            "signatures/signature.bin",
            lambda data: bytes([data[0] ^ 1]) + data[1:],
            CapsuleIntegrityError,
            "signature is invalid",
        ),
        (
            "signatures/manifest.json",
            lambda data: canonical_json_bytes(
                {**json.loads(data), "manifest_sha256": "e" * 64}
            ),
            CapsuleIntegrityError,
            "signature manifest digest",
        ),
    ],
)
def test_tampered_manifest_artifact_signature_and_schema_are_rejected(
    tmp_path, member_suffix, mutate, error, message
) -> None:
    archive = export_run_capsule(_bundle(signed=True), tmp_path / "original.zip")

    def mutate_selected(path: str, data: bytes) -> bytes:
        return mutate(data) if path.endswith(member_suffix) else data

    tampered = _rewrite_archive(archive, tmp_path / "tampered.zip", mutate_selected)
    with pytest.raises(error, match=message):
        verify_run_capsule(tampered)


@pytest.mark.parametrize("attack", ["traversal", "symlink", "duplicate", "zip_bomb"])
def test_unsafe_archive_paths_links_duplicates_and_compression_are_rejected(
    tmp_path, attack
) -> None:
    archive_path = tmp_path / f"{attack}.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=(
            zipfile.ZIP_DEFLATED if attack == "zip_bomb" else zipfile.ZIP_STORED
        ),
    ) as archive:
        if attack == "traversal":
            archive.writestr("capsule_fixture/../escape.json", b"{}")
        elif attack == "symlink":
            info = zipfile.ZipInfo("capsule_fixture/capsule.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
        elif attack == "duplicate":
            archive.writestr("capsule_fixture/capsule.json", b"{}")
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("capsule_fixture/capsule.json", b"{}")
        else:
            archive.writestr("capsule_fixture/capsule.json", b"0" * (512 * 1024))
    with pytest.raises(CapsuleArchiveError):
        verify_run_capsule(archive_path)


def test_clean_checkout_and_observed_input_drift_are_reported_without_execution(
    tmp_path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "fixture@example.com")
    _git(checkout, "config", "user.name", "Fixture")
    tracked = checkout / "README.md"
    tracked.write_text("fixture\n", encoding="utf-8")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-qm", "fixture")
    head = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    fixture = _fixture()
    input_payload = dict(fixture["input_lock"])
    input_payload["git"] = {
        "base_commit": head,
        "dirty": False,
        "files": [
            {
                "path": "README.md",
                "sha256": hashlib.sha256(tracked.read_bytes()).hexdigest(),
                "byte_count": tracked.stat().st_size,
            }
        ],
    }
    archive = export_run_capsule(
        _bundle(signed=False, input_payload=input_payload), tmp_path / "checkout.zip"
    )
    matched = verify_run_capsule(
        archive,
        checkout=checkout,
        observed_inputs={"agent_profile.sha256": "a" * 64},
    )
    assert matched.status is VerificationStatus.VERIFIED
    assert all(finding.status is FindingStatus.MATCHED for finding in matched.findings)

    tracked.write_text("changed\n", encoding="utf-8")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-qm", "changed")
    drifted = verify_run_capsule(
        archive,
        checkout=checkout,
        observed_inputs={
            "agent_profile.sha256": "9" * 64,
            "acp.capabilities_sha256": "8" * 64,
            "environment.container": None,
        },
    )
    assert drifted.status is VerificationStatus.DRIFTED
    statuses = {finding.field: finding.status for finding in drifted.findings}
    assert statuses["git.base_commit"] is FindingStatus.DRIFTED
    assert statuses["git.files:README.md"] is FindingStatus.DRIFTED
    assert statuses["agent_profile.sha256"] is FindingStatus.DRIFTED
    assert statuses["acp.capabilities_sha256"] is FindingStatus.DRIFTED
    assert statuses["environment.container"] is FindingStatus.OMITTED

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CapsuleCheckoutError, match="must be clean"):
        verify_run_capsule(archive, checkout=checkout)


def _git(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
