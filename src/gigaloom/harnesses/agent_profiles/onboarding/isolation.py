"""Platform-owned network-deny launchers for managed ACP probes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Protocol, runtime_checkable


_MACOS_NETWORK_DENY_PROFILE = " ".join(
    (
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        '(allow network-bind (local ip "localhost:*"))',
        '(allow network-inbound (local ip "localhost:*"))',
        '(allow network-outbound (remote ip "localhost:*"))',
    )
)


@runtime_checkable
class ManagedAcpNetworkIsolationPort(Protocol):
    """Wrap one exact ACP command in an enforcing network-deny boundary."""

    @property
    def mechanism(self) -> str:
        """Return the bounded platform mechanism identity."""

    def wrap(
        self,
        command: tuple[str, ...],
        *,
        workspace: Path,
        native_home: Path,
    ) -> tuple[str, ...]:
        """Return a tokenized command that denies child network access."""


@dataclass(frozen=True, slots=True)
class ManagedAcpNetworkIsolation:
    """Pinned platform launcher used only for initialize-only probes."""

    mechanism: str
    executable: str

    def __post_init__(self) -> None:
        if self.mechanism not in {"linux_bwrap", "macos_sandbox_exec"}:
            raise ValueError("managed ACP isolation mechanism is invalid")
        path = Path(self.executable)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("managed ACP isolation executable is unavailable")

    def wrap(
        self,
        command: tuple[str, ...],
        *,
        workspace: Path,
        native_home: Path,
    ) -> tuple[str, ...]:
        """Wrap a pinned command without invoking a shell or ambient PATH."""
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("managed ACP isolation requires a pinned command")
        resolved_workspace = workspace.resolve(strict=True)
        resolved_home = native_home.resolve(strict=True)
        if self.mechanism == "macos_sandbox_exec":
            return (
                self.executable,
                "-p",
                _MACOS_NETWORK_DENY_PROFILE,
                *command,
            )
        return (
            self.executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(resolved_home),
            str(resolved_home),
            "--bind",
            str(resolved_workspace),
            str(resolved_workspace),
            "--chdir",
            str(resolved_workspace),
            "--",
            *command,
        )


def discover_managed_acp_network_isolation(
    *,
    platform_id: str | None = None,
) -> ManagedAcpNetworkIsolation | None:
    """Discover only fixed, system-owned isolation executables."""
    platform = platform_id or sys.platform
    candidates = (
        (("/usr/bin/sandbox-exec",), "macos_sandbox_exec")
        if platform == "darwin"
        else (("/usr/bin/bwrap", "/bin/bwrap"), "linux_bwrap")
        if platform.startswith("linux")
        else ((), "unsupported")
    )
    paths, mechanism = candidates
    for candidate in paths:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return ManagedAcpNetworkIsolation(
                mechanism=mechanism,
                executable=str(path.resolve(strict=True)),
            )
    return None


__all__ = [
    "ManagedAcpNetworkIsolation",
    "ManagedAcpNetworkIsolationPort",
    "discover_managed_acp_network_isolation",
]
