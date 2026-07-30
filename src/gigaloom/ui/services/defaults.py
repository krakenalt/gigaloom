"""Default-value projections owned by the UI application layer."""

from gigaloom.config import DEFAULT_MODEL_HINTS, HarnessConfig


def fallback_models(config: HarnessConfig) -> list[str]:
    """Return ordered configured and built-in model hints."""
    return list(
        dict.fromkeys(
            model for model in (config.default_model, *DEFAULT_MODEL_HINTS) if model
        )
    )
