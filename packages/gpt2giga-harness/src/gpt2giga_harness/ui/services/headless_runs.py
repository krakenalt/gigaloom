"""In-memory ownership records for UI-started headless runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
from typing import Any


@dataclass
class ActiveHeadlessRun:
    """Bind an active task to the cancellation signal exposed by the UI."""

    task: asyncio.Task[Any]
    cancel_event: threading.Event
