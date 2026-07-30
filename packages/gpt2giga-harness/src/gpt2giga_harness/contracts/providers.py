"""Stable provider and protocol selection contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any


class GigaChatApiMode(str, Enum):
    """Explicit GigaChat Chat Completions backend contract."""

    V1 = "v1"
    V2 = "v2"


class GigaChatBuiltinTool(str, Enum):
    """GigaChat v2 tools that execute inside the upstream model service."""

    WEB_SEARCH = "web_search"
    URL_CONTENT_EXTRACTION = "url_content_extraction"
    CODE_INTERPRETER = "code_interpreter"
    IMAGE_GENERATE = "image_generate"
    MODEL_3D_GENERATE = "model_3d_generate"


GIGACHAT_BUILTIN_TOOLS = tuple(GigaChatBuiltinTool)


def parse_api_mode(value: str | GigaChatApiMode | None) -> GigaChatApiMode:
    """Parse a CLI/UI API mode value."""
    if isinstance(value, GigaChatApiMode):
        return value
    if value is None or not str(value).strip():
        return GigaChatApiMode.V2
    return GigaChatApiMode(str(value).strip().lower())


def parse_builtin_tools(value: Any) -> tuple[GigaChatBuiltinTool, ...]:
    """Parse a unique list of supported GigaChat built-in tool names."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("builtin_tools must be a list")
    tools: list[GigaChatBuiltinTool] = []
    for item in value:
        try:
            tool = (
                item
                if isinstance(item, GigaChatBuiltinTool)
                else GigaChatBuiltinTool(str(item).strip().lower())
            )
        except ValueError as exc:
            raise ValueError(f"Unsupported built-in tool: {item}") from exc
        if tool not in tools:
            tools.append(tool)
    return tuple(tools)


for _public_contract in (
    GigaChatApiMode,
    GigaChatBuiltinTool,
    parse_api_mode,
    parse_builtin_tools,
):
    _public_contract.__module__ = "gpt2giga_harness.types"

__all__ = [
    "GIGACHAT_BUILTIN_TOOLS",
    "GigaChatApiMode",
    "GigaChatBuiltinTool",
    "parse_api_mode",
    "parse_builtin_tools",
]
