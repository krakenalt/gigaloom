# ruff: noqa: E402, F401, F403, F405
"""Grouped integration transaction public boundary."""

from .actions import _GroupActionsMixin
from .helpers import *
from .models import *
from .persistence import _GroupPersistenceMixin
from .preview import _GroupPreviewMixin


class GroupedIntegrationService(
    _GroupPreviewMixin,
    _GroupActionsMixin,
    _GroupPersistenceMixin,
):
    """Coordinate exact child transactions through durable compensation."""


__all__ = [
    "GroupedIntegrationService",
    "IntegrationGroupConflictError",
    "IntegrationGroupError",
    "IntegrationGroupNotFoundError",
    "IntegrationGroupRecord",
    "IntegrationGroupStatus",
    "integration_group_record_to_dict",
]
