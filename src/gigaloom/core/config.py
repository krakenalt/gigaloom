"""Environment-backed configuration primitives."""

from __future__ import annotations

import json
import os


DEFAULT_PROXY_URL = "http://127.0.0.1:8090"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8091
DEFAULT_UI_REMOTE_ABSOLUTE_TTL_SECONDS = 8 * 60 * 60
DEFAULT_UI_REMOTE_IDLE_TTL_SECONDS = 30 * 60
DEFAULT_PROXY_START_TIMEOUT_SECONDS = 15.0
DEFAULT_HARNESS_TIMEOUT_SECONDS = 3600.0
DEFAULT_HARNESS_DATA_DIR = "~/.gigaloom"
DEFAULT_CHAT_MODEL = "GigaChat-3.5-432B-A28B"
DEFAULT_TITLE_MODEL = "GigaChat-3-Lightning"
DEFAULT_MODEL_HINTS = (
    DEFAULT_CHAT_MODEL,
    DEFAULT_TITLE_MODEL,
    "GigaChat-3-Ultra",
    "GigaChat-2-Pro",
    "GigaChat",
)


def pass_model_env_note() -> str | None:
    """Return a note when PASS_MODEL indicates model override behavior."""
    value = env_first("GPT2GIGA_PASS_MODEL")
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"false", "0", "no", "off"}:
        return (
            "GPT2GIGA_PASS_MODEL=False; requested model may be overridden "
            "by upstream GIGACHAT_MODEL."
        )
    return f"GPT2GIGA_PASS_MODEL={value}"


def env_first(*names: str) -> str | None:
    """Return the first non-empty environment value."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def normalize_proxy_url(value: str) -> str:
    """Normalize a proxy URL without changing its scheme or path."""
    return value.strip().rstrip("/")


def parse_int(value: str | None, default: int) -> int:
    """Parse an integer or return the supplied default."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None, default: float) -> float:
    """Parse a float or return the supplied default."""
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_bool(value: str | None, default: bool) -> bool:
    """Parse a conventional boolean value or return the supplied default."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_csv(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated, case-normalized tuple."""
    if value is None:
        return ()
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def parse_oidc_role_map(value: str | None) -> tuple[tuple[str, str], ...]:
    """Parse a default-deny JSON object mapping exact subjects to roles."""
    if value is None:
        return ()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("GIGALOOM_UI_OIDC_ROLE_MAP must be a JSON object") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError("GIGALOOM_UI_OIDC_ROLE_MAP must be a non-empty JSON object")
    normalized: list[tuple[str, str]] = []
    for subject, role in payload.items():
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 512
            or role not in {"viewer", "operator"}
        ):
            raise ValueError(
                "OIDC role map entries require an exact subject and viewer/operator role"
            )
        normalized.append((subject, role))
    return tuple(sorted(normalized))
