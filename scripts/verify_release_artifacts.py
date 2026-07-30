#!/usr/bin/env python3
"""Verify one immutable Python and npm release-candidate bundle."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any, Mapping, Sequence
import zipfile

from verify_web_artifact_parity import (
    read_npm_web_tree,
    read_wheel_web_tree,
    verify_parity,
)


SCHEMA_VERSION = "gigaloom-release-candidate-v1"
UV_OUTPUT_MARKER = ".gitignore"
EXPECTED_MANIFEST_FIELDS = {
    "git_tag",
    "npm_package",
    "npm_version",
    "python_distribution",
    "python_version",
    "release",
}
RELEASE_RE = re.compile(
    r"(?P<base>\d+\.\d+\.\d+)"
    r"(?:-(?P<stage>alpha|beta|rc)\.(?P<number>[1-9]\d*))?"
)
REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
EVIDENCE_FILES = {
    "licenses.json": "_build/licenses.json",
    "release.json": None,
    "sbom.cdx.json": "_build/sbom.cdx.json",
    "web-provenance.json": "_build/provenance.json",
}
_MAX_MEMBER_BYTES = 32 * 1024 * 1024


class ReleaseArtifactError(RuntimeError):
    """Raised when a candidate artifact set is incomplete or inconsistent."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_object(content: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseArtifactError(f"{label} is malformed") from exc
    if not isinstance(payload, Mapping):
        raise ReleaseArtifactError(f"{label} must be a JSON object")
    return payload


def _expected_python_version(release: str) -> str:
    match = RELEASE_RE.fullmatch(release)
    if match is None:
        raise ReleaseArtifactError(f"unsupported release version: {release!r}")
    stage = match.group("stage")
    if stage is None:
        return match.group("base")
    python_stage = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
    return f"{match.group('base')}{python_stage}{match.group('number')}"


def _release_identity(path: Path) -> tuple[dict[str, str], bytes]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ReleaseArtifactError(f"release manifest is unavailable: {path}") from exc
    payload = _json_object(content, label="release manifest")
    if set(payload) != EXPECTED_MANIFEST_FIELDS or any(
        not isinstance(payload[field], str) for field in EXPECTED_MANIFEST_FIELDS
    ):
        raise ReleaseArtifactError("release manifest does not match schema v1")
    identity = {field: payload[field] for field in EXPECTED_MANIFEST_FIELDS}
    if identity["python_distribution"] != "gigaloom":
        raise ReleaseArtifactError("unexpected Python distribution")
    if identity["npm_package"] != "@gigaloom/web":
        raise ReleaseArtifactError("unexpected npm package")
    if identity["git_tag"] != f"v{identity['release']}":
        raise ReleaseArtifactError("release tag does not match canonical release")
    if identity["npm_version"] != identity["release"]:
        raise ReleaseArtifactError("npm version does not match canonical release")
    if identity["python_version"] != _expected_python_version(identity["release"]):
        raise ReleaseArtifactError("Python version does not map from canonical release")
    return identity, content


def _single_artifact(root: Path, pattern: str, *, label: str) -> Path:
    matches = sorted(path for path in root.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise ReleaseArtifactError(
            f"expected exactly one {label} matching {pattern!r}, found {len(matches)}"
        )
    if matches[0].is_symlink():
        raise ReleaseArtifactError(f"{label} must not be a symlink")
    return matches[0]


def _safe_member_name(value: str) -> str:
    if not value or "\\" in value:
        raise ReleaseArtifactError(f"invalid archive member path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ReleaseArtifactError(f"unsafe archive member path: {value!r}")
    return value


def _tar_members(path: Path, suffixes: Mapping[str, str]) -> dict[str, bytes]:
    matched: dict[str, bytes] = {}
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                _safe_member_name(member.name)
                for key, suffix in suffixes.items():
                    if not member.name.endswith(suffix):
                        continue
                    if key in matched or not member.isfile():
                        raise ReleaseArtifactError(
                            f"sdist has duplicate or non-file {suffix}"
                        )
                    if member.size > _MAX_MEMBER_BYTES:
                        raise ReleaseArtifactError(
                            f"sdist member is too large: {suffix}"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ReleaseArtifactError(
                            f"sdist member is unreadable: {suffix}"
                        )
                    matched[key] = stream.read()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseArtifactError(f"sdist is missing or malformed: {path}") from exc
    missing = sorted(set(suffixes) - set(matched))
    if missing:
        raise ReleaseArtifactError(f"sdist is missing required evidence: {missing}")
    return matched


def _metadata_identity(content: bytes, *, label: str) -> tuple[str, str]:
    metadata = BytesParser().parsebytes(content)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ReleaseArtifactError(f"{label} metadata has no name or version")
    return name, version


def _artifact_record(path: Path, *, kind: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(f"candidate file is not regular: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ReleaseArtifactError(f"candidate file is unavailable: {path}") from exc
    return {
        "bytes": len(content),
        "filename": path.name,
        "kind": kind,
        "sha256": _sha256(content),
    }


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def verify_candidate(
    *,
    release_path: Path,
    artifact_dir: Path,
    expected_source_revision: str | None = None,
) -> dict[str, Any]:
    """Verify a complete candidate directory and return its stable manifest."""
    identity, release_content = _release_identity(release_path)
    if not artifact_dir.is_dir() or artifact_dir.is_symlink():
        raise ReleaseArtifactError("candidate artifact directory is unavailable")

    python_base = identity["python_distribution"].replace("-", "_")
    npm_base = identity["npm_package"].removeprefix("@").replace("/", "-")
    wheel = _single_artifact(
        artifact_dir,
        f"{python_base}-{identity['python_version']}-*.whl",
        label="wheel",
    )
    sdist = _single_artifact(
        artifact_dir,
        f"{python_base}-{identity['python_version']}.tar.gz",
        label="sdist",
    )
    npm = _single_artifact(
        artifact_dir,
        f"{npm_base}-{identity['npm_version']}.tgz",
        label="npm tarball",
    )
    allowed_names = {
        wheel.name,
        sdist.name,
        npm.name,
        *EVIDENCE_FILES,
        "candidate-manifest.json",
        "SHA256SUMS",
    }
    uv_output_marker = artifact_dir / UV_OUTPUT_MARKER
    if uv_output_marker.exists() or uv_output_marker.is_symlink():
        if (
            uv_output_marker.is_symlink()
            or not uv_output_marker.is_file()
            or uv_output_marker.read_bytes() != b"*"
        ):
            raise ReleaseArtifactError("uv output marker is invalid")
    actual_names = {
        path.name
        for path in artifact_dir.iterdir()
        if path.name not in {"candidate-manifest.json", "SHA256SUMS", UV_OUTPUT_MARKER}
    }
    expected_names = allowed_names - {"candidate-manifest.json", "SHA256SUMS"}
    if actual_names != expected_names:
        raise ReleaseArtifactError(
            "candidate file inventory differs from the release allowlist"
        )

    wheel_files = read_wheel_web_tree(wheel)
    npm_files = read_npm_web_tree(npm)
    parity = dict(
        verify_parity(
            wheel_files,
            npm_files,
            release_manifest=release_content,
        )
    )
    source_revision = parity["source_revision"]
    if (
        not isinstance(source_revision, str)
        or REVISION_RE.fullmatch(source_revision) is None
    ):
        raise ReleaseArtifactError("candidate source revision is invalid")
    if (
        expected_source_revision is not None
        and source_revision != expected_source_revision
    ):
        raise ReleaseArtifactError("candidate source revision does not match checkout")

    wheel_metadata = _single_artifact_member(
        wheel,
        suffix=".dist-info/METADATA",
    )
    if _metadata_identity(wheel_metadata, label="wheel") != (
        identity["python_distribution"],
        identity["python_version"],
    ):
        raise ReleaseArtifactError("wheel identity does not match release manifest")

    sdist_evidence = _tar_members(
        sdist,
        {
            "content": "/src/gigaloom/ui/web/assets/_build/content-manifest.json",
            "metadata": "/PKG-INFO",
            "provenance": "/src/gigaloom/ui/web/assets/_build/provenance.json",
        },
    )
    if _metadata_identity(sdist_evidence["metadata"], label="sdist") != (
        identity["python_distribution"],
        identity["python_version"],
    ):
        raise ReleaseArtifactError("sdist identity does not match release manifest")
    if (
        sdist_evidence["content"] != wheel_files["_build/content-manifest.json"]
        or sdist_evidence["provenance"] != wheel_files["_build/provenance.json"]
    ):
        raise ReleaseArtifactError("sdist Web evidence differs from wheel and npm")

    npm_metadata = _json_object(
        _single_npm_member(npm, "package/package.json"),
        label="npm package metadata",
    )
    if (
        npm_metadata.get("name") != identity["npm_package"]
        or npm_metadata.get("version") != identity["npm_version"]
        or npm_metadata.get("private") is not False
        or not isinstance(npm_metadata.get("publishConfig"), Mapping)
        or npm_metadata["publishConfig"].get("access") != "public"
    ):
        raise ReleaseArtifactError("npm identity does not match release manifest")

    evidence_records = []
    for filename, embedded_name in sorted(EVIDENCE_FILES.items()):
        evidence = artifact_dir / filename
        try:
            evidence_content = evidence.read_bytes()
        except OSError as exc:
            raise ReleaseArtifactError(
                f"candidate evidence is unavailable: {filename}"
            ) from exc
        expected = (
            release_content if embedded_name is None else wheel_files[embedded_name]
        )
        if evidence_content != expected:
            raise ReleaseArtifactError(
                f"candidate evidence differs from embedded bytes: {filename}"
            )
        evidence_records.append(_artifact_record(evidence, kind="evidence"))

    artifact_records = [
        _artifact_record(npm, kind="npm"),
        _artifact_record(sdist, kind="sdist"),
        _artifact_record(wheel, kind="wheel"),
        *evidence_records,
    ]
    artifact_records.sort(key=lambda item: item["filename"])
    return {
        "artifacts": artifact_records,
        "frontend": parity,
        "release": identity,
        "schema_version": SCHEMA_VERSION,
        "source_revision": source_revision,
    }


def _single_artifact_member(path: Path, *, suffix: str) -> bytes:
    matched: list[bytes] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                _safe_member_name(member.filename)
                if member.filename.endswith(suffix):
                    if member.is_dir() or member.file_size > _MAX_MEMBER_BYTES:
                        raise ReleaseArtifactError(
                            f"wheel has invalid member ending in {suffix}"
                        )
                    matched.append(archive.read(member))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseArtifactError(f"wheel is missing or malformed: {path}") from exc
    if len(matched) != 1:
        raise ReleaseArtifactError(
            f"wheel must contain exactly one member ending in {suffix}"
        )
    return matched[0]


def _single_npm_member(path: Path, name: str) -> bytes:
    try:
        with tarfile.open(path, "r:*") as archive:
            matches = [member for member in archive.getmembers() if member.name == name]
            if (
                len(matches) != 1
                or not matches[0].isfile()
                or matches[0].size > _MAX_MEMBER_BYTES
            ):
                raise ReleaseArtifactError(f"npm tarball has invalid {name}")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise ReleaseArtifactError(f"npm tarball member is unreadable: {name}")
            return stream.read()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseArtifactError(
            f"npm tarball is missing or malformed: {path}"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Verify candidate artifacts and optionally write stable evidence files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("dist/release-candidate"),
    )
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args(argv)
    if (
        args.source_revision is not None
        and REVISION_RE.fullmatch(args.source_revision) is None
    ):
        parser.error("--source-revision must be a full lowercase Git SHA")
    if args.checksums is not None and args.output is None:
        parser.error("--checksums requires --output")
    artifact_dir = args.artifact_dir.resolve()
    if args.output is not None and args.output.resolve() != (
        artifact_dir / "candidate-manifest.json"
    ):
        parser.error("--output must be candidate-manifest.json in --artifact-dir")
    if args.checksums is not None and args.checksums.resolve() != (
        artifact_dir / "SHA256SUMS"
    ):
        parser.error("--checksums must be SHA256SUMS in --artifact-dir")

    try:
        manifest = verify_candidate(
            release_path=args.release.resolve(),
            artifact_dir=artifact_dir,
            expected_source_revision=args.source_revision,
        )
    except (KeyError, OSError, ReleaseArtifactError) as exc:
        parser.error(str(exc))
    encoded = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    if args.output is not None:
        _write_atomic(args.output.resolve(), encoded)
    if args.checksums is not None:
        paths = [
            args.artifact_dir.resolve() / item["filename"]
            for item in manifest["artifacts"]
        ]
        paths.append(args.output.resolve())
        lines = [
            f"{_sha256(path.read_bytes())}  {path.name}\n"
            for path in sorted(paths, key=lambda item: item.name)
        ]
        _write_atomic(args.checksums.resolve(), "".join(lines).encode())
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
