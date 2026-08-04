"""Safe startup environment for the reviewed managed gpt2giga profile."""

from __future__ import annotations

import os
from pathlib import Path
import secrets

from gigaloom.config import HarnessConfig


_SAFE_HOST_ENVIRONMENT = frozenset(
    {
        "PATH",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)
_UPSTREAM_CREDENTIALS = (
    "GIGACHAT_CREDENTIALS",
    "GIGACHAT_ACCESS_TOKEN",
    "GIGACHAT_USER",
)


def managed_gpt2giga_environment(
    config: HarnessConfig,
    *,
    managed_root: Path,
    gateway_api_key: str,
    public_model_alias: str | None = None,
) -> dict[str, str]:
    """Build isolated sidecar inputs without provider calls or secret persistence."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("GIGACHAT_") or name in _SAFE_HOST_ENVIRONMENT
    }
    if not any(environment.get(name, "").strip() for name in _UPSTREAM_CREDENTIALS):
        raise ValueError("gateway_upstream_credentials_unavailable")
    environment.update(
        {
            "HOME": os.fspath(managed_root / "startup-home"),
            "GPT2GIGA_MODE": "DEV",
            "GPT2GIGA_ENABLE_API_KEY_AUTH": "True",
            "GPT2GIGA_API_KEY": gateway_api_key,
            "GIGALOOM_MODEL_KEY": secrets.token_urlsafe(32),
            "GPT2GIGA_GIGACHAT_API_MODE": "v2",
            "GPT2GIGA_NORMALIZATION_MODE": "on",
            "GPT2GIGA_LEGACY_CHAT_FALLBACK": "False",
            "GPT2GIGA_PASS_MODEL": "True",
        }
    )
    selected_model = public_model_alias or config.default_model
    if selected_model:
        environment["GIGACHAT_MODEL"] = selected_model
    return environment


__all__ = ["managed_gpt2giga_environment"]
