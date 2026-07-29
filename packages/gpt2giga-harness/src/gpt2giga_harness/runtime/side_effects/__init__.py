"""Harness-owned durable side-effect coordination."""

from gpt2giga_harness.runtime.side_effects.executor import HarnessSideEffectExecutor
from gpt2giga_harness.runtime.side_effects.repository import SideEffectsRepository

HarnessSideEffectExecutor.__module__ = __name__

__all__ = ["HarnessSideEffectExecutor", "SideEffectsRepository"]
