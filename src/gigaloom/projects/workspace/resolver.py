"""Safe workspace path resolution."""

from gigaloom.safe_paths import resolve_operator_path


def resolve_workspace(value: str | None) -> str | None:
    """Resolve an optional workspace path for subprocess cwd."""
    if value is None:
        return None
    return str(resolve_operator_path(value))
