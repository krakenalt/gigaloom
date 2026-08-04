"""Optional environment mutation services used by the UI container."""

from gigaloom.config import HarnessConfig
from gigaloom.environment_actions import (
    EnvironmentCommitError,
    EnvironmentCommitService,
)
from gigaloom.environment_pull_requests import (
    EnvironmentPullRequestError,
    EnvironmentPullRequestService,
)
from gigaloom.environment_push import EnvironmentPushError, EnvironmentPushService


def optional_commit_service(
    config: HarnessConfig,
    service: EnvironmentCommitService | None,
) -> EnvironmentCommitService | None:
    """Return the supplied commit service or construct one when supported."""
    if service is not None:
        return service
    try:
        return EnvironmentCommitService(config.data_dir)
    except EnvironmentCommitError:
        return None


def optional_push_service(
    config: HarnessConfig,
    service: EnvironmentPushService | None,
) -> EnvironmentPushService | None:
    """Return the supplied push service or construct one when supported."""
    if service is not None:
        return service
    try:
        return EnvironmentPushService(config.data_dir)
    except EnvironmentPushError:
        return None


def optional_pull_request_service(
    config: HarnessConfig,
    service: EnvironmentPullRequestService | None,
) -> EnvironmentPullRequestService | None:
    """Return the supplied pull-request service or construct one when supported."""
    if service is not None:
        return service
    try:
        return EnvironmentPullRequestService(config.data_dir)
    except EnvironmentPullRequestError:
        return None


__all__ = [
    "optional_commit_service",
    "optional_pull_request_service",
    "optional_push_service",
]
