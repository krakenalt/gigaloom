"""Managed native Codex configuration materialization."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

from gigaloom.contracts.codex_instructions import codex_developer_instructions
from gigaloom.managed_mcp import write_startup_config
from gigaloom.types import HarnessContext, HarnessRequest

CODEX_PROVIDER_NAME = "gigaloom"


def write_codex_config(
    codex_home: Path,
    request: HarnessRequest,
    context: HarnessContext,
) -> str:
    """Write one request-bound managed native Codex configuration."""
    custom = request.extra.get("developer_instructions")
    return write_codex_config_values(
        codex_home,
        model=request.model or context.default_model or "GigaChat",
        base_url=context.api_base_url(request.api_mode),
        developer_instructions=(custom if isinstance(custom, str) else ""),
    )


def write_codex_config_values(
    codex_home: Path,
    *,
    model: str,
    base_url: str,
    developer_instructions: str = "",
    preserve_existing_instructions: bool = False,
) -> str:
    """Write managed Codex values with composed developer instructions."""
    composed = (
        _existing_developer_instructions(codex_home)
        if preserve_existing_instructions
        else None
    ) or codex_developer_instructions(developer_instructions)
    config = (
        f'model = "{_toml_escape(model)}"\n'
        f'model_provider = "{CODEX_PROVIDER_NAME}"\n'
        'model_reasoning_effort = "none"\n\n'
        f"developer_instructions = {json.dumps(composed, ensure_ascii=False)}\n\n"
        f"[model_providers.{CODEX_PROVIDER_NAME}]\n"
        f'name = "{CODEX_PROVIDER_NAME}"\n'
        f'base_url = "{_toml_escape(base_url)}"\n'
        'env_key = "GPT2GIGA_API_KEY"\n'
        'wire_api = "responses"\n'
        "supports_websockets = false\n"
    )
    return write_startup_config("codex-cli", codex_home, config)


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _existing_developer_instructions(codex_home: Path) -> str | None:
    try:
        payload = tomllib.loads(
            (codex_home / "config.toml").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    value = payload.get("developer_instructions")
    return value if isinstance(value, str) and value.strip() else None
