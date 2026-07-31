"""Application service for scheduled automation."""

from __future__ import annotations

from typing import Any
from gigaloom.automation.evaluations.api import FilesystemHarnessEvalStore
from gigaloom.automation.ports import RuntimeCoordinationStore
from gigaloom.automation.ports import DurableJobDispatcher
from .dispatch import ScheduleDispatchMixin
from .lifecycle import ScheduleLifecycleMixin


class ScheduleService(ScheduleLifecycleMixin, ScheduleDispatchMixin):
    """Coordinate schedule lifecycle and durable dispatch."""

    def __init__(
        self,
        *,
        runtime_store: RuntimeCoordinationStore,
        runner: Any,
        dispatcher: DurableJobDispatcher,
        eval_store: FilesystemHarnessEvalStore,
    ) -> None:
        self.runtime_store = runtime_store
        self.runner = runner
        self.dispatcher = dispatcher
        self.eval_store = eval_store
