# ruff: noqa: E402, F401, F403, F405
"""Integration lifecycle service public boundary."""

from .lifecycle_core import _LifecycleCoreMixin
from .lifecycle_models import *
from .lifecycle_policy import _LifecyclePolicyMixin
from .lifecycle_projection import _LifecycleProjectionMixin


class IntegrationLifecycleService(
    _LifecycleCoreMixin,
    _LifecyclePolicyMixin,
    _LifecycleProjectionMixin,
):
    """Apply safe lifecycle transitions across integration flows and groups."""


__all__ = [
    "IntegrationLifecycleAction",
    "IntegrationLifecycleConflictError",
    "IntegrationLifecycleError",
    "IntegrationLifecycleNotFoundError",
    "IntegrationLifecycleService",
]
