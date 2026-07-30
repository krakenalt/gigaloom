"""Verify byte parity between Python-embedded and npm Web asset trees."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile
from typing import Any, Mapping, Sequence
import zipfile


CONTENT_FORMAT_VERSION = "gigaloom-web-content-manifest-v1"
CONTENT_MANIFEST = "_build/content-manifest.json"
RUNTIME_MANIFEST = "manifest.json"
WHEEL_PREFIX = "gigaloom/ui/web/assets/"
NPM_PREFIX = "package/dist/"
_MAX_FILES = 512
_MAX_BYTES = 32 * 1024 * 1024


class WebArtifactParityError(RuntimeError):
    """Report malformed, unsafe, or non-identical Web artifacts."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_relative_name(value: str) -> str:
    if not value or "\\" in value:
        raise WebArtifactParityError(f"invalid artifact path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise WebArtifactParityError(f"unsafe artifact path: {value!r}")
    return value


def _bounded(files: Mapping[str, bytes], *, label: str) -> dict[str, bytes]:
    if len(files) > _MAX_FILES:
        raise WebArtifactParityError(f"{label} exceeds the file-count bound")
    if sum(len(content) for content in files.values()) > _MAX_BYTES:
        raise WebArtifactParityError(f"{label} exceeds the byte bound")
    if not files:
        raise WebArtifactParityError(f"{label} contains no Web assets")
    return dict(files)


def _check_next_file(
    files: Mapping[str, bytes],
    *,
    size: int,
    total_bytes: int,
    label: str,
) -> int:
    if len(files) >= _MAX_FILES:
        raise WebArtifactParityError(f"{label} exceeds the file-count bound")
    if size < 0 or size > _MAX_BYTES or total_bytes + size > _MAX_BYTES:
        raise WebArtifactParityError(f"{label} exceeds the byte bound")
    return total_bytes + size


def _directory_tree(root: Path, *, label: str) -> dict[str, bytes]:
    if not root.is_dir() or root.is_symlink():
        raise WebArtifactParityError(f"{label} is not a regular directory")
    root = root.resolve()
    files: dict[str, bytes] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise WebArtifactParityError(f"{label} contains a symlink")
        if path.is_file():
            name = _safe_relative_name(path.relative_to(root).as_posix())
            total_bytes = _check_next_file(
                files,
                size=path.stat().st_size,
                total_bytes=total_bytes,
                label=label,
            )
            files[name] = path.read_bytes()
        elif not path.is_dir():
            raise WebArtifactParityError(f"{label} contains a non-regular entry")
    return _bounded(files, label=label)


def _wheel_tree(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total_bytes = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.filename.startswith(WHEEL_PREFIX):
                    continue
                name = member.filename.removeprefix(WHEEL_PREFIX)
                if not name or member.is_dir():
                    continue
                _safe_relative_name(name)
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG}:
                    raise WebArtifactParityError(
                        f"wheel Web tree contains a non-regular entry: {name}"
                    )
                if name in files:
                    raise WebArtifactParityError(
                        f"wheel Web tree contains a duplicate: {name}"
                    )
                total_bytes = _check_next_file(
                    files,
                    size=member.file_size,
                    total_bytes=total_bytes,
                    label="wheel Web tree",
                )
                files[name] = archive.read(member)
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        raise WebArtifactParityError(f"wheel is missing or malformed: {path}") from exc
    return _bounded(files, label="wheel Web tree")


def _npm_tree(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total_bytes = 0
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if not member.name.startswith(NPM_PREFIX):
                    continue
                name = member.name.removeprefix(NPM_PREFIX)
                if not name or member.isdir():
                    continue
                _safe_relative_name(name)
                if not member.isfile():
                    raise WebArtifactParityError(
                        f"npm Web tree contains a non-regular entry: {name}"
                    )
                if name in files:
                    raise WebArtifactParityError(
                        f"npm Web tree contains a duplicate: {name}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise WebArtifactParityError(
                        f"npm Web tree member is unreadable: {name}"
                    )
                total_bytes = _check_next_file(
                    files,
                    size=member.size,
                    total_bytes=total_bytes,
                    label="npm Web tree",
                )
                files[name] = extracted.read()
    except (FileNotFoundError, tarfile.TarError) as exc:
        raise WebArtifactParityError(
            f"npm tarball is missing or malformed: {path}"
        ) from exc
    return _bounded(files, label="npm Web tree")


def _json_object(content: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebArtifactParityError(f"{label} is malformed") from exc
    if not isinstance(payload, Mapping):
        raise WebArtifactParityError(f"{label} must be a JSON object")
    return payload


def _named_digest(files: Mapping[str, bytes], names: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name])
        digest.update(b"\0")
    return digest.hexdigest()


def _verify_tree(
    files: Mapping[str, bytes],
    *,
    label: str,
    release_manifest: bytes,
) -> Mapping[str, Any]:
    try:
        content_bytes = files[CONTENT_MANIFEST]
        runtime_bytes = files[RUNTIME_MANIFEST]
    except KeyError as exc:
        raise WebArtifactParityError(f"{label} is missing {exc.args[0]}") from exc
    content = _json_object(content_bytes, label=f"{label} content manifest")
    runtime = _json_object(runtime_bytes, label=f"{label} runtime manifest")
    if content.get("format_version") != CONTENT_FORMAT_VERSION:
        raise WebArtifactParityError(f"{label} content manifest version is unsupported")
    if content.get("release_manifest_sha256") != _sha256(release_manifest):
        raise WebArtifactParityError(f"{label} release manifest digest does not match")

    source_revision = content.get("source_revision")
    if (
        not isinstance(source_revision, str)
        or len(source_revision) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in source_revision)
    ):
        raise WebArtifactParityError(f"{label} source revision is invalid")

    raw_records = content.get("files")
    described_names = set(files) - {CONTENT_MANIFEST, RUNTIME_MANIFEST}
    if not isinstance(raw_records, Mapping) or set(raw_records) != described_names:
        raise WebArtifactParityError(f"{label} content inventory is incomplete")
    for name in sorted(described_names):
        _safe_relative_name(name)
        record = raw_records.get(name)
        file_content = files[name]
        if (
            not isinstance(record, Mapping)
            or record.get("bytes") != len(file_content)
            or record.get("sha256") != _sha256(file_content)
        ):
            raise WebArtifactParityError(f"{label} content record is invalid: {name}")
    if content.get("content_sha256") != _named_digest(files, sorted(described_names)):
        raise WebArtifactParityError(f"{label} aggregate content digest is invalid")

    build = runtime.get("build")
    content_record = build.get("content") if isinstance(build, Mapping) else None
    if (
        not isinstance(content_record, Mapping)
        or content_record.get("path") != CONTENT_MANIFEST
        or content_record.get("bytes") != len(content_bytes)
        or content_record.get("sha256") != _sha256(content_bytes)
        or content.get("output_sha256") != build.get("output_sha256")
    ):
        raise WebArtifactParityError(f"{label} runtime/content binding is invalid")
    provenance = _json_object(
        files.get("_build/provenance.json", b""),
        label=f"{label} provenance",
    )
    if provenance.get("source_revision") != source_revision or provenance.get(
        "release_manifest_sha256"
    ) != content.get("release_manifest_sha256"):
        raise WebArtifactParityError(f"{label} provenance identity is inconsistent")
    return content


def verify_parity(
    python_files: Mapping[str, bytes],
    npm_files: Mapping[str, bytes],
    *,
    release_manifest: bytes,
) -> Mapping[str, Any]:
    """Verify two complete trees and return their shared content identity."""
    python_content = _verify_tree(
        python_files,
        label="Python Web tree",
        release_manifest=release_manifest,
    )
    npm_content = _verify_tree(
        npm_files,
        label="npm Web tree",
        release_manifest=release_manifest,
    )
    if set(python_files) != set(npm_files):
        raise WebArtifactParityError("Python and npm Web file inventories differ")
    mismatched = [
        name for name in sorted(python_files) if python_files[name] != npm_files[name]
    ]
    if mismatched:
        raise WebArtifactParityError(f"Python and npm Web bytes differ: {mismatched}")
    if python_content != npm_content:
        raise WebArtifactParityError("Python and npm content manifests differ")
    return {
        "content_sha256": python_content["content_sha256"],
        "file_count": len(python_files),
        "release_manifest_sha256": python_content["release_manifest_sha256"],
        "source_revision": python_content["source_revision"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run directory or archive parity verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--python-assets", type=Path)
    parser.add_argument("--npm-assets", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--npm-tarball", type=Path)
    arguments = parser.parse_args(argv)
    directory_mode = (
        arguments.python_assets is not None or arguments.npm_assets is not None
    )
    archive_mode = arguments.wheel is not None or arguments.npm_tarball is not None
    if directory_mode == archive_mode:
        parser.error("choose exactly one directory pair or one archive pair")
    if directory_mode:
        if arguments.python_assets is None or arguments.npm_assets is None:
            parser.error("--python-assets and --npm-assets are required together")
        python_files = _directory_tree(arguments.python_assets, label="Python Web tree")
        npm_files = _directory_tree(arguments.npm_assets, label="npm Web tree")
    else:
        if arguments.wheel is None or arguments.npm_tarball is None:
            parser.error("--wheel and --npm-tarball are required together")
        python_files = _wheel_tree(arguments.wheel)
        npm_files = _npm_tree(arguments.npm_tarball)
    try:
        release_manifest = arguments.release.read_bytes()
    except FileNotFoundError as exc:
        raise WebArtifactParityError(
            f"release manifest is missing: {arguments.release}"
        ) from exc
    report = verify_parity(
        python_files,
        npm_files,
        release_manifest=release_manifest,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
