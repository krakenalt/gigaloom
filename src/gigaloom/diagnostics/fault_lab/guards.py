"""Safety admission for isolated recovery fault sandboxes."""

from __future__ import annotations

from pathlib import Path


class ActiveDataRootRejectedError(ValueError):
    """Raised when fault mode could overlap active product state."""


def admit_sandbox_parent(
    sandbox_parent: str | Path,
    *,
    active_data_root: str | Path | None,
) -> Path:
    """Reject equal, ancestor, descendant, symlink, and missing parents."""
    supplied = Path(sandbox_parent).expanduser()
    if supplied.is_symlink():
        raise ActiveDataRootRejectedError(
            "fault sandbox parent must be a real directory"
        )
    parent = supplied.resolve(strict=True)
    if not parent.is_dir():
        raise ActiveDataRootRejectedError(
            "fault sandbox parent must be a real directory"
        )
    if active_data_root is None:
        return parent
    active = Path(active_data_root).expanduser().resolve(strict=False)
    if parent == active or _contains(parent, active) or _contains(active, parent):
        raise ActiveDataRootRejectedError(
            "fault sandbox must not overlap the active data directory"
        )
    return parent


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
