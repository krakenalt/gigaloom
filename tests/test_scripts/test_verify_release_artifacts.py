from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _module():
    sys.path.insert(0, str(SCRIPTS))
    try:
        specification = importlib.util.spec_from_file_location(
            "verify_release_artifacts",
            SCRIPTS / "verify_release_artifacts.py",
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def _canonical_json(payload: object) -> bytes:
    return f"{json.dumps(payload, indent=2, sort_keys=True)}\n".encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _named_digest(files: dict[str, bytes], names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name])
        digest.update(b"\0")
    return digest.hexdigest()


def _web_tree(release_manifest: bytes, source_revision: str) -> dict[str, bytes]:
    release_digest = _sha256(release_manifest)
    files = {
        "assets/app-deadbeef.js": b"console.log('candidate');\n",
        "index.html": b"<title>GigaLoom</title>\n",
        "_build/licenses.json": b'{"packages":[]}\n',
        "_build/sbom.cdx.json": b'{"components":[]}\n',
        "_build/provenance.json": _canonical_json(
            {
                "release_manifest_sha256": release_digest,
                "source_revision": source_revision,
            }
        ),
    }
    runtime_names = ["assets/app-deadbeef.js", "index.html"]
    described_names = sorted(files)
    output_digest = _named_digest(files, runtime_names)
    content_manifest = _canonical_json(
        {
            "content_sha256": _named_digest(files, described_names),
            "files": {
                name: {
                    "bytes": len(files[name]),
                    "sha256": _sha256(files[name]),
                }
                for name in described_names
            },
            "format_version": "gigaloom-web-content-manifest-v1",
            "output_sha256": output_digest,
            "release_manifest_sha256": release_digest,
            "source_revision": source_revision,
        }
    )
    files["_build/content-manifest.json"] = content_manifest
    files["manifest.json"] = _canonical_json(
        {
            "build": {
                "content": {
                    "bytes": len(content_manifest),
                    "path": "_build/content-manifest.json",
                    "sha256": _sha256(content_manifest),
                },
                "output_sha256": output_digest,
            }
        }
    )
    return files


def _add_tar_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def _candidate(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    release = {
        "git_tag": "v0.6.0-alpha.1",
        "npm_package": "@gigaloom/web",
        "npm_version": "0.6.0-alpha.1",
        "python_distribution": "gigaloom",
        "python_version": "0.6.0a1",
        "release": "0.6.0-alpha.1",
    }
    release_content = _canonical_json(release)
    release_path = tmp_path / "release.json"
    release_path.write_bytes(release_content)
    source_revision = "a" * 40
    files = _web_tree(release_content, source_revision)
    artifacts = tmp_path / "dist" / "release-candidate"
    artifacts.mkdir(parents=True)

    wheel = artifacts / "gigaloom-0.6.0a1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"gigaloom/ui/web/assets/{name}", content)
        archive.writestr(
            "gigaloom-0.6.0a1.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: gigaloom\nVersion: 0.6.0a1\n\n",
        )

    sdist = artifacts / "gigaloom-0.6.0a1.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        _add_tar_bytes(
            archive,
            "gigaloom-0.6.0a1/PKG-INFO",
            b"Metadata-Version: 2.4\nName: gigaloom\nVersion: 0.6.0a1\n\n",
        )
        for name in (
            "_build/content-manifest.json",
            "_build/provenance.json",
        ):
            _add_tar_bytes(
                archive,
                f"gigaloom-0.6.0a1/src/gigaloom/ui/web/assets/{name}",
                files[name],
            )

    npm = artifacts / "gigaloom-web-0.6.0-alpha.1.tgz"
    with tarfile.open(npm, "w:gz") as archive:
        for name, content in files.items():
            _add_tar_bytes(archive, f"package/dist/{name}", content)
        _add_tar_bytes(
            archive,
            "package/package.json",
            _canonical_json(
                {
                    "name": "@gigaloom/web",
                    "private": False,
                    "publishConfig": {"access": "public"},
                    "version": "0.6.0-alpha.1",
                }
            ),
        )

    (artifacts / "release.json").write_bytes(release_content)
    for filename, embedded in (
        ("licenses.json", "_build/licenses.json"),
        ("sbom.cdx.json", "_build/sbom.cdx.json"),
        ("web-provenance.json", "_build/provenance.json"),
    ):
        (artifacts / filename).write_bytes(files[embedded])
    return {
        "artifact_dir": artifacts,
        "release": release_path,
        "source_revision": source_revision,
    }


def test_candidate_bundle_is_verified_and_manifest_is_deterministic(tmp_path: Path):
    module = _module()
    fixture = _candidate(tmp_path)

    first = module.verify_candidate(
        release_path=fixture["release"],
        artifact_dir=fixture["artifact_dir"],
        expected_source_revision=fixture["source_revision"],
    )
    second = module.verify_candidate(
        release_path=fixture["release"],
        artifact_dir=fixture["artifact_dir"],
        expected_source_revision=fixture["source_revision"],
    )

    assert first == second
    assert first["schema_version"] == "gigaloom-release-candidate-v1"
    assert first["source_revision"] == "a" * 40
    assert {item["kind"] for item in first["artifacts"]} == {
        "evidence",
        "npm",
        "sdist",
        "wheel",
    }
    assert len(first["frontend"]["content_sha256"]) == 64


def test_candidate_accepts_only_exact_uv_output_marker(tmp_path: Path):
    module = _module()
    fixture = _candidate(tmp_path)
    artifact_dir = fixture["artifact_dir"]
    assert isinstance(artifact_dir, Path)
    marker = artifact_dir / ".gitignore"
    marker.write_bytes(b"*")

    module.verify_candidate(
        release_path=fixture["release"],
        artifact_dir=artifact_dir,
        expected_source_revision=fixture["source_revision"],
    )

    marker.write_bytes(b"*\n")
    with pytest.raises(
        module.ReleaseArtifactError,
        match="uv output marker is invalid",
    ):
        module.verify_candidate(
            release_path=fixture["release"],
            artifact_dir=artifact_dir,
            expected_source_revision=fixture["source_revision"],
        )


def test_candidate_cli_writes_bound_manifest_and_checksums(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    module = _module()
    fixture = _candidate(tmp_path)
    artifacts = fixture["artifact_dir"]
    output = artifacts / "candidate-manifest.json"
    checksums = artifacts / "SHA256SUMS"

    assert (
        module.main(
            [
                "--release",
                str(fixture["release"]),
                "--artifact-dir",
                str(artifacts),
                "--source-revision",
                str(fixture["source_revision"]),
                "--output",
                str(output),
                "--checksums",
                str(checksums),
            ]
        )
        == 0
    )

    assert json.loads(output.read_text(encoding="utf-8"))["source_revision"] == "a" * 40
    checksum_names = {
        line.split("  ", 1)[1]
        for line in checksums.read_text(encoding="utf-8").splitlines()
    }
    assert "candidate-manifest.json" in checksum_names
    assert "SHA256SUMS" not in checksum_names
    assert json.loads(capsys.readouterr().out)["schema_version"].endswith("-v1")


def test_candidate_rejects_changed_evidence_or_source_revision(tmp_path: Path):
    module = _module()
    fixture = _candidate(tmp_path)
    artifact_dir = fixture["artifact_dir"]
    assert isinstance(artifact_dir, Path)
    evidence = artifact_dir / "licenses.json"
    evidence.write_text('{"changed":true}\n', encoding="utf-8")

    with pytest.raises(
        module.ReleaseArtifactError,
        match="differs from embedded bytes",
    ):
        module.verify_candidate(
            release_path=fixture["release"],
            artifact_dir=fixture["artifact_dir"],
            expected_source_revision=fixture["source_revision"],
        )

    fixture = _candidate(tmp_path / "other")
    with pytest.raises(
        module.ReleaseArtifactError,
        match="does not match checkout",
    ):
        module.verify_candidate(
            release_path=fixture["release"],
            artifact_dir=fixture["artifact_dir"],
            expected_source_revision="b" * 40,
        )
