"""Domain-independent secret redaction primitives."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping


SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
)
SAFE_NUMERIC_USAGE_KEYS = frozenset(
    {
        "cached_input_tokens",
        "cached_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "reasoning_output_tokens",
        "reasoning_tokens",
        "thoughts_tokens",
        "tool_tokens",
        "total_tokens",
    }
)
SAFE_USAGE_DETAIL_KEYS = frozenset(
    {
        "completion_tokens_details",
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
    }
)
REDACTED = "<redacted>"
SECRET_ENV_NAMES = (
    "GIGACHAT_CREDENTIALS",
    "GIGACHAT_ACCESS_TOKEN",
    "GPT2GIGA_API_KEY",
    "GIGALOOM_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}"),
    re.compile(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)
_SECRET_TEXT_KEY = (
    r"[A-Za-z0-9_.-]*(?:api[_-]?key|authorization|cookie|credentials|"
    r"database[_-]?url|db[_-]?url|password|passwd|private[_-]?key|secret|token)"
    r"[A-Za-z0-9_.-]*"
)
# Only attempt a key match at a token boundary. Without this guard, a long
# non-secret token makes the leading and trailing wildcards backtrack quadratically.
_SECRET_JSON_VALUE_PATTERN = re.compile(
    rf"(?P<prefix>(?<![A-Za-z0-9_.-])[\"']?{_SECRET_TEXT_KEY}[\"']?\s*:\s*)"
    rf"(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    rf"(?P<prefix>(?<![A-Za-z0-9_.-]){_SECRET_TEXT_KEY}\s*(?:=|:\s+)\s*)"
    rf"(?:(?P<quote>[\"'])(?P<quoted>[^\r\n]*?)(?P=quote)|"
    rf"(?P<bare>[^\s&,;\r\n]+))",
    re.IGNORECASE,
)
_URL_CREDENTIALS_PATTERN = re.compile(
    r"\b(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+@)",
    re.IGNORECASE,
)


def redact_secrets(value: Any) -> Any:
    """Recursively redact secret-looking mapping values."""
    redaction_hook = getattr(value, "__gpt2giga_redacted__", None)
    if callable(redaction_hook):
        return redaction_hook()
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if _is_safe_numeric_usage(key_text, item):
                redacted[str(key)] = item
            elif key_text in SAFE_USAGE_DETAIL_KEYS and isinstance(item, Mapping):
                redacted[str(key)] = redact_secrets(item)
            elif any(part in key_text for part in SECRET_KEY_PARTS):
                redacted[str(key)] = REDACTED
            else:
                redacted[str(key)] = redact_secrets(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def _is_safe_numeric_usage(key: str, value: Any) -> bool:
    return (
        key in SAFE_NUMERIC_USAGE_KEYS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _redact_secret_text(text: str) -> str:
    redacted = text
    for name in SECRET_ENV_NAMES:
        value = os.getenv(name)
        if value and value != "0":
            redacted = redacted.replace(value, REDACTED)
    redacted = _SECRET_JSON_VALUE_PATTERN.sub(_redacted_assignment, redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(_redacted_assignment, redacted)
    redacted = _URL_CREDENTIALS_PATTERN.sub(
        lambda match: f"{match.group('scheme')}{REDACTED}@",
        redacted,
    )
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _redacted_assignment(match: re.Match[str]) -> str:
    quote = match.groupdict().get("quote") or ""
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


redact_secrets.__module__ = "gigaloom.types"

__all__ = [
    "REDACTED",
    "SAFE_NUMERIC_USAGE_KEYS",
    "SAFE_USAGE_DETAIL_KEYS",
    "SECRET_ENV_NAMES",
    "SECRET_KEY_PARTS",
    "SECRET_VALUE_PATTERNS",
    "redact_secrets",
]
