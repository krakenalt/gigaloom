"""Traversal-safe bounded extraction for ACP binary archives."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

from gigaloom.contracts import ExtractionLimitsV1
from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    ensure_private_directory,
)


_COPY_CHUNK_BYTES = 64 * 1024


def extract_binary_archive(
    archive_path: Path,
    destination: Path,
    *,
    archive_name: str,
    command: str,
    limits: ExtractionLimitsV1,
) -> str:
    """Extract only regular files/directories and return the executable path."""
    ensure_private_directory(destination)
    lowered = archive_name.lower()
    if lowered.endswith(".zip"):
        _extract_zip(archive_path, destination, limits)
    elif lowered.endswith((".tar.gz", ".tgz", ".tar")):
        _extract_tar(archive_path, destination, limits)
    else:
        raise AgentInstallError("binary_archive_format_unsupported")
    executable = _safe_member(command)
    executable_path = destination.joinpath(*executable.parts)
    if (
        not executable_path.exists()
        or executable_path.is_symlink()
        or not executable_path.is_file()
    ):
        raise AgentInstallError("binary_command_missing_from_archive")
    executable_path.chmod(0o700)
    return executable.as_posix()


def _extract_zip(
    archive_path: Path, destination: Path, limits: ExtractionLimitsV1
) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            files = [item for item in infos if not item.is_dir()]
            _validate_count_and_paths(
                (item.filename for item in infos), len(files), limits
            )
            if sum(item.file_size for item in files) > limits.max_bytes:
                raise AgentInstallError("binary_archive_expanded_too_large")
            extracted = 0
            for info in infos:
                relative = _safe_member(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise AgentInstallError("binary_archive_special_member")
                target = destination.joinpath(*relative.parts)
                if info.is_dir():
                    ensure_private_directory(target)
                    continue
                ensure_private_directory(target.parent)
                with archive.open(info) as source, target.open("xb") as sink:
                    extracted = _copy_bounded(source, sink, extracted, limits.max_bytes)
                target.chmod(0o600)
    except AgentInstallError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise AgentInstallError("binary_archive_invalid") from error


def _extract_tar(
    archive_path: Path, destination: Path, limits: ExtractionLimitsV1
) -> None:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = archive.getmembers()
            files = [item for item in members if item.isfile()]
            _validate_count_and_paths(
                (item.name for item in members), len(files), limits
            )
            if any(not (item.isdir() or item.isfile()) for item in members):
                raise AgentInstallError("binary_archive_special_member")
            if sum(item.size for item in files) > limits.max_bytes:
                raise AgentInstallError("binary_archive_expanded_too_large")
            extracted = 0
            for member in members:
                relative = _safe_member(member.name)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    ensure_private_directory(target)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise AgentInstallError("binary_archive_invalid")
                ensure_private_directory(target.parent)
                with source, target.open("xb") as sink:
                    extracted = _copy_bounded(source, sink, extracted, limits.max_bytes)
                target.chmod(0o600)
    except AgentInstallError:
        raise
    except (OSError, ValueError, tarfile.TarError) as error:
        raise AgentInstallError("binary_archive_invalid") from error


def _validate_count_and_paths(
    names, file_count: int, limits: ExtractionLimitsV1
) -> None:  # noqa: ANN001
    if file_count > limits.max_files:
        raise AgentInstallError("binary_archive_file_limit_exceeded")
    normalized = tuple(_safe_member(name) for name in names)
    if any(len(path.parts) > limits.max_path_depth for path in normalized):
        raise AgentInstallError("binary_archive_path_depth_exceeded")
    folded = tuple(path.as_posix().casefold() for path in normalized)
    if len(folded) != len(set(folded)):
        raise AgentInstallError("binary_archive_duplicate_member")


def _safe_member(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise AgentInstallError("binary_archive_unsafe_path")
    normalized = value[:-1] if value.endswith("/") else value
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AgentInstallError("binary_archive_unsafe_path")
    return path


def _copy_bounded(source, sink, total: int, maximum: int) -> int:  # noqa: ANN001
    while True:
        chunk = source.read(_COPY_CHUNK_BYTES)
        if not chunk:
            return total
        total += len(chunk)
        if total > maximum:
            raise AgentInstallError("binary_archive_expanded_too_large")
        sink.write(chunk)
        sink.flush()
    return total
