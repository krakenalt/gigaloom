"""Typed FastAPI dependencies for application-scoped services."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request

from gigaloom.config import HarnessConfig
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions import HarnessSessionStore
from gigaloom.ui.container import AppServices


def install_app_services(app: FastAPI, services: AppServices) -> None:
    """Bind the typed container and temporary legacy state compatibility."""
    app.state.harness_services = services
    for name, value in services.legacy_state().items():
        setattr(app.state, name, value)


def app_services(app: FastAPI) -> AppServices:
    """Return the typed service container installed on an application."""
    services = getattr(app.state, "harness_services", None)
    if not isinstance(services, AppServices):
        raise RuntimeError("Harness application services are not installed")
    return services


async def get_app_services(request: Request) -> AppServices:
    """Resolve application services for a request."""
    return app_services(request.app)


AppServicesDependency = Annotated[AppServices, Depends(get_app_services)]


async def get_config(services: AppServicesDependency) -> HarnessConfig:
    """Resolve immutable Harness configuration."""
    return services.config


HarnessConfigDependency = Annotated[HarnessConfig, Depends(get_config)]


async def get_session_store(
    services: AppServicesDependency,
) -> HarnessSessionStore:
    """Resolve the authoritative session store."""
    return services.session_store


SessionStoreDependency = Annotated[HarnessSessionStore, Depends(get_session_store)]


async def get_runtime_store(
    services: AppServicesDependency,
) -> RuntimeCoordinationStore | None:
    """Resolve the optional durable runtime store."""
    return services.runtime_store


RuntimeStoreDependency = Annotated[
    RuntimeCoordinationStore | None,
    Depends(get_runtime_store),
]
