"""Optional gpt2giga gateway discovery and lifecycle."""

from .preset import gpt2giga_preset_available, require_gpt2giga_preset
from .proxy import ensure_proxy_available

__all__ = [
    "ensure_proxy_available",
    "gpt2giga_preset_available",
    "require_gpt2giga_preset",
]
