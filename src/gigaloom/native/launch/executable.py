"""Absolute executable resolution for declarative native agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import stat

from gigaloom.native.launch.contracts import NativeAgentLaunchSpec


class NativeExecutableKind(str, Enum):
    """Reviewed executable form selected without a shell."""

    POSIX = "posix"
    WINDOWS_EXECUTABLE = "windows_executable"
    WINDOWS_SHIM = "windows_shim"


class NativeExecutableStatus(str, Enum):
    """Content-free executable resolution result."""

    READY = "ready"
    MISSING = "missing"
    NON_EXECUTABLE = "non_executable"
    UNSAFE = "unsafe"
    DRIFTED = "drifted"


@dataclass(frozen=True)
class NativeExecutableIdentity:
    """Pinned regular-file identity revalidated immediately before launch."""

    requested_name: str
    path: Path
    kind: NativeExecutableKind
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class NativeExecutableResolution:
    """Ready executable identity or a typed content-free failure."""

    status: NativeExecutableStatus
    identity: NativeExecutableIdentity | None = None

    def __post_init__(self) -> None:
        if self.status is NativeExecutableStatus.READY:
            if self.identity is None:
                raise ValueError("ready native executable requires an identity")
        elif self.identity is not None:
            raise ValueError("failed native executable cannot contain an identity")


def resolve_profile_executable(
    spec: NativeAgentLaunchSpec,
    *,
    environment: Mapping[str, str],
    facade_executable: str | os.PathLike[str] | None,
    platform: str,
) -> NativeExecutableResolution:
    """Resolve the first available declared name and pin its exact file identity."""
    search_path = environment.get("PATH", os.defpath)
    facade = _resolved_facade(facade_executable)
    for executable_name in spec.executable_names:
        for candidate, kind in _candidates(
            executable_name,
            search_path=search_path,
            environment=environment,
            platform=platform,
        ):
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            try:
                resolved = candidate.resolve(strict=True)
                file_stat = resolved.stat()
            except OSError:
                return NativeExecutableResolution(NativeExecutableStatus.NON_EXECUTABLE)
            if not stat.S_ISREG(file_stat.st_mode):
                return NativeExecutableResolution(NativeExecutableStatus.NON_EXECUTABLE)
            if facade is not None and _same_file(resolved, facade):
                return NativeExecutableResolution(NativeExecutableStatus.UNSAFE)
            if platform != "win32" and not os.access(resolved, os.X_OK):
                return NativeExecutableResolution(NativeExecutableStatus.NON_EXECUTABLE)
            return NativeExecutableResolution(
                NativeExecutableStatus.READY,
                NativeExecutableIdentity(
                    requested_name=executable_name,
                    path=resolved,
                    kind=kind,
                    device=file_stat.st_dev,
                    inode=file_stat.st_ino,
                    mode=file_stat.st_mode,
                    size=file_stat.st_size,
                    modified_ns=file_stat.st_mtime_ns,
                ),
            )
    return NativeExecutableResolution(NativeExecutableStatus.MISSING)


def revalidate_executable_identity(
    identity: NativeExecutableIdentity,
) -> NativeExecutableStatus:
    """Reject replacement, type, permission, or path drift before process start."""
    try:
        file_stat = identity.path.stat()
    except OSError:
        return NativeExecutableStatus.DRIFTED
    current = (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )
    expected = (
        identity.device,
        identity.inode,
        identity.mode,
        identity.size,
        identity.modified_ns,
    )
    if current != expected or not stat.S_ISREG(file_stat.st_mode):
        return NativeExecutableStatus.DRIFTED
    if identity.kind is NativeExecutableKind.POSIX and not os.access(
        identity.path,
        os.X_OK,
    ):
        return NativeExecutableStatus.DRIFTED
    return NativeExecutableStatus.READY


def _candidates(
    executable_name: str,
    *,
    search_path: str,
    environment: Mapping[str, str],
    platform: str,
) -> tuple[tuple[Path, NativeExecutableKind], ...]:
    directories = tuple(
        Path(item or os.curdir) for item in search_path.split(os.pathsep)
    )
    if platform != "win32":
        return tuple(
            (directory / executable_name, NativeExecutableKind.POSIX)
            for directory in directories
        )
    suffix = Path(executable_name).suffix.casefold()
    if suffix:
        names = (executable_name,)
    else:
        extensions = tuple(
            item.casefold()
            for item in environment.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            if item
        )
        names = tuple(executable_name + extension for extension in extensions)
    candidates: list[tuple[Path, NativeExecutableKind]] = []
    for directory in directories:
        for name in names:
            extension = Path(name).suffix.casefold()
            kind = (
                NativeExecutableKind.WINDOWS_SHIM
                if extension in {".cmd", ".bat"}
                else NativeExecutableKind.WINDOWS_EXECUTABLE
            )
            candidates.append((directory / name, kind))
    return tuple(candidates)


def _resolved_facade(
    facade_executable: str | os.PathLike[str] | None,
) -> Path | None:
    if facade_executable is None:
        return None
    try:
        candidate = Path(facade_executable)
        if not candidate.is_absolute() and candidate.parent == Path("."):
            return None
        return candidate.resolve(strict=True)
    except OSError:
        return None


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right
