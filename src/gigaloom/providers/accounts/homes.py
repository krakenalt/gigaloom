"""Private provider-home preparation for native authentication commands."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import stat

from .broker import ProviderAuthenticationOperationError


def ensure_provider_config_home(home: Path, directory_name: str) -> Path:
    """Create and validate the private config directory required by a native CLI."""
    config_home = home / directory_name
    try:
        config_home.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        metadata = config_home.lstat()
    except OSError as exc:
        raise ProviderAuthenticationOperationError(
            "provider_config_home_invalid"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProviderAuthenticationOperationError("provider_config_home_invalid")
    with suppress(OSError):
        config_home.chmod(0o700)
    return config_home
