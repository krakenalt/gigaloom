"""Validation primitives for reviewed external MCP descriptors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import urlsplit

from gigaloom.secrets import (
    SecretReference,
    secret_reference_from_dict,
)


_IMPLICIT_INSTALLERS = {"bunx", "dnx", "npx", "pipx", "pnpx", "uvx"}
_SHELLS = {"bash", "cmd", "dash", "fish", "powershell", "pwsh", "sh", "zsh"}


def _validate_argv(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("external MCP command must use explicit argv")
    normalized = tuple(values)
    if any(
        not isinstance(item, str)
        or not item
        or any(character in item for character in ("\0", "\n", "\r"))
        for item in normalized
    ):
        raise ValueError("external MCP argv is invalid")
    if normalized:
        executable = PurePosixPath(normalized[0].replace("\\", "/")).name.lower()
        if executable in _IMPLICIT_INSTALLERS:
            raise ValueError("external MCP implicit installer commands are forbidden")
        if (
            executable in _SHELLS
            and len(normalized) > 1
            and normalized[1]
            in {
                "-c",
                "/c",
                "-command",
            }
        ):
            raise ValueError("external MCP shell command strings are forbidden")
    return normalized


def _validate_secret_bindings(
    bindings: Mapping[str, SecretReference], pattern: re.Pattern[str], field_name: str
) -> Mapping[str, SecretReference]:
    if not isinstance(bindings, Mapping):
        raise ValueError(f"external MCP {field_name} bindings must be an object")
    normalized: dict[str, SecretReference] = {}
    for name, reference in bindings.items():
        if not isinstance(name, str) or not pattern.fullmatch(name):
            raise ValueError(f"external MCP {field_name} binding name is invalid")
        if not isinstance(reference, SecretReference):
            raise ValueError(f"external MCP {field_name} values must be SecretRef")
        normalized[name] = reference
    return dict(sorted(normalized.items()))


def _selection_secret_mapping(
    value: Any, field_name: str
) -> Mapping[str, SecretReference]:
    if not isinstance(value, Mapping):
        raise ValueError(f"external MCP {field_name} references must be an object")
    result: dict[str, SecretReference] = {}
    for name, reference in value.items():
        if not isinstance(name, str) or not isinstance(reference, Mapping):
            raise ValueError(f"external MCP {field_name} reference is invalid")
        result[name] = secret_reference_from_dict(reference)
    return result


def _normalized_names(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character.isspace() for character in value)
        for value in normalized
    ):
        raise ValueError(f"external MCP {label} is invalid")
    return normalized


def _target_safe_id(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._:@+~-]+", "-", name).strip("-")[:180]
    if not slug:
        raise ValueError("official MCP name cannot form a target identity")
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"external-mcp-{slug}-{digest}"


def _canonical_https_url(value: Any) -> str:
    if not isinstance(value, str) or "{" in value or "}" in value:
        raise ValueError("external MCP URL must be exact")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("external MCP URL must be canonical HTTPS")
    return value


def _canonical_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("external MCP download origin must be canonical HTTPS")
    return f"https://{parsed.netloc}"


def _canonical_git_url(value: Any) -> str:
    url = _canonical_https_url(value)
    parsed = urlsplit(url)
    if parsed.hostname != "github.com":
        raise ValueError("external MCP Git source must be GitHub HTTPS")
    path = parsed.path.rstrip("/").removesuffix(".git")
    if len(path.strip("/").split("/")) != 2:
        raise ValueError("external MCP Git repository URL is invalid")
    return f"https://github.com{path}"


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
