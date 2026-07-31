"""Typed project catalog failures."""


class ProjectCatalogError(ValueError):
    """Base failure for invalid project catalog operations."""


class ProjectCatalogNotFoundError(KeyError):
    """Raised when a catalog project does not exist."""


class ProjectCatalogConflictError(ProjectCatalogError):
    """Raised when a catalog mutation conflicts with current state."""


class ProjectCatalogCapacityError(ProjectCatalogError):
    """Raised when the bounded authoritative catalog is full."""
