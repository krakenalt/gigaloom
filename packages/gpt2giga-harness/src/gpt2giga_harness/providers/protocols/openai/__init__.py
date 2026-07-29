"""OpenAI-compatible provider protocol."""

from .compatible import OpenAICompatibleProbeBackend
from .upstream import build_openai_compatible_upstream_adapter

__all__ = [
    "OpenAICompatibleProbeBackend",
    "build_openai_compatible_upstream_adapter",
]
