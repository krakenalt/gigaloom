"""FastAPI lifecycle ownership for application services."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from gpt2giga_harness.ui.async_execution import stop_monitor

if TYPE_CHECKING:
    from gpt2giga_harness.ui.container import AppServices


def create_app_lifespan(services: AppServices):
    """Return the lifecycle context bound to one typed service container."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        monitor = asyncio.create_task(
            services.async_diagnostics.monitor_event_loop(),
            name="harness-event-loop-lag",
        )
        try:
            yield
        finally:
            await stop_monitor(monitor)

    return lifespan
