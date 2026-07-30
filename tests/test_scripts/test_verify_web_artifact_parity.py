from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _module():
    specification = importlib.util.spec_from_file_location(
        "verify_web_artifact_parity",
        REPO_ROOT / "scripts/verify_web_artifact_parity.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _canonical_json(payload: object) -> bytes:
    return f"{json.dumps(payload, indent=2)}\n".encode()


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


def _fixture_tree(release_manifest: bytes) -> dict[str, bytes]:
    source_revision = "a" * 40
    release_digest = _sha256(release_manifest)
    files = {
        "assets/app-deadbeef.js": b"console.log('fixture');\n",
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


def test_directory_trees_require_exact_byte_parity(tmp_path: Path):
    module = _module()
    release_manifest = b'{"release":"0.6.0-alpha.1"}\n'
    files = _fixture_tree(release_manifest)

    report = module.verify_parity(
        files,
        dict(files),
        release_manifest=release_manifest,
    )

    assert report["file_count"] == len(files)
    assert report["source_revision"] == "a" * 40

    with pytest.raises(module.WebArtifactParityError, match="release manifest digest"):
        module.verify_parity(
            files,
            dict(files),
            release_manifest=b'{"release":"other"}\n',
        )

    changed = dict(files)
    changed["index.html"] += b"changed"
    with pytest.raises(module.WebArtifactParityError, match="content record"):
        module.verify_parity(
            files,
            changed,
            release_manifest=release_manifest,
        )


def test_archive_mode_reads_wheel_and_npm_without_extraction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    module = _module()
    release_manifest = b'{"release":"0.6.0-alpha.1"}\n'
    release_path = tmp_path / "release.json"
    release_path.write_bytes(release_manifest)
    files = _fixture_tree(release_manifest)
    wheel = tmp_path / "gigaloom.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"gigaloom/ui/web/assets/{name}", content)
    npm_tarball = tmp_path / "gigaloom-web.tgz"
    with tarfile.open(npm_tarball, "w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(f"package/dist/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    result = module.main(
        [
            "--release",
            str(release_path),
            "--wheel",
            str(wheel),
            "--npm-tarball",
            str(npm_tarball),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["file_count"] == len(files)
    assert not list(tmp_path.glob("package"))


def test_npm_archive_rejects_symlinked_web_members(tmp_path: Path):
    module = _module()
    npm_tarball = tmp_path / "unsafe.tgz"
    with tarfile.open(npm_tarball, "w:gz") as archive:
        member = tarfile.TarInfo("package/dist/index.html")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../secret"
        archive.addfile(member)

    with pytest.raises(module.WebArtifactParityError, match="non-regular"):
        module._npm_tree(npm_tarball)
