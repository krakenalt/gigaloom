"""Fail-closed upstream schema sanitization and proxy responses."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from fastapi.responses import JSONResponse

from gpt2giga_harness.skills.catalog_proxy.contracts import (
    SKILLS_PROXY_MAX_AUDITS,
    SKILLS_PROXY_MAX_FILE_PATHS,
    SKILLS_PROXY_MAX_RESPONSE_BYTES,
    SkillsProxyUpstreamResponse,
    _LIST_ITEM_FIELDS,
)
from gpt2giga_harness.skills.catalog_proxy.transport import (
    _ProxyFailure,
    _validate_upstream_url,
)


def _validated_upstream_payload(response: SkillsProxyUpstreamResponse) -> Any:
    try:
        _validate_upstream_url(response.final_url)
    except ValueError as exc:
        raise _ProxyFailure(502, "proxy.redirect_rejected") from exc
    if response.redirected:
        raise _ProxyFailure(502, "proxy.redirect_rejected")
    if response.status_code in {401, 403}:
        raise _ProxyFailure(503, "proxy.upstream_auth_failed")
    if response.status_code == 429:
        retry_after = next(
            (
                value
                for key, value in response.headers.items()
                if key.casefold() == "retry-after" and value.isdigit()
            ),
            None,
        )
        raise _ProxyFailure(
            429,
            "proxy.upstream_rate_limited",
            headers={"Retry-After": retry_after} if retry_after is not None else None,
        )
    if response.status_code == 404:
        raise _ProxyFailure(404, "proxy.not_found")
    if not 200 <= response.status_code < 300:
        raise _ProxyFailure(502, "proxy.upstream_failed")
    if (
        not isinstance(response.body, bytes)
        or len(response.body) > SKILLS_PROXY_MAX_RESPONSE_BYTES
    ):
        raise _ProxyFailure(502, "proxy.response_too_large")
    try:
        return json.loads(
            response.body.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _ProxyFailure(502, "proxy.invalid_payload") from exc


def _sanitize_listing(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise _ProxyFailure(502, "proxy.invalid_payload")
    if "pagination" in payload:
        if set(payload) != {"data", "pagination"}:
            raise _ProxyFailure(502, "proxy.schema_drift")
        pagination = payload["pagination"]
        if not isinstance(pagination, Mapping) or set(pagination) != {
            "page",
            "perPage",
            "total",
            "hasMore",
        }:
            raise _ProxyFailure(502, "proxy.schema_drift")
        return {
            "data": _sanitize_items(payload["data"]),
            "pagination": dict(pagination),
        }
    if set(payload) != {"data", "query", "searchType", "count", "durationMs"}:
        raise _ProxyFailure(502, "proxy.schema_drift")
    return {
        "data": _sanitize_items(payload["data"]),
        "query": payload["query"],
        "searchType": payload["searchType"],
        "count": payload["count"],
        "durationMs": payload["durationMs"],
    }


def _sanitize_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise _ProxyFailure(502, "proxy.invalid_payload")
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) - _LIST_ITEM_FIELDS:
            raise _ProxyFailure(502, "proxy.schema_drift")
        result.append({key: item[key] for key in item if key in _LIST_ITEM_FIELDS})
    return result


def _sanitize_detail(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise _ProxyFailure(502, "proxy.invalid_payload")
    required = {"id", "source", "slug", "installs", "hash"}
    if not required <= set(payload) or set(payload) - (required | {"files"}):
        raise _ProxyFailure(502, "proxy.schema_drift")
    sanitized = {key: payload[key] for key in sorted(required)}
    if (
        not isinstance(sanitized["hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", sanitized["hash"]) is None
    ):
        raise _ProxyFailure(502, "proxy.invalid_payload")
    files = payload.get("files")
    if files is None:
        sanitized["files"] = None
        return sanitized
    if not isinstance(files, list) or len(files) > SKILLS_PROXY_MAX_FILE_PATHS:
        raise _ProxyFailure(502, "proxy.invalid_payload")
    sanitized["files"] = [_sanitize_file_path(item) for item in files]
    return sanitized


def _sanitize_file_path(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "contents"}:
        raise _ProxyFailure(502, "proxy.schema_drift")
    path = value.get("path")
    if (
        not isinstance(path, str)
        or not 1 <= len(path) <= 512
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 for char in path)
    ):
        raise _ProxyFailure(502, "proxy.invalid_payload")
    return {"path": path}


def _sanitize_curated(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "data",
        "totalOwners",
        "totalSkills",
        "generatedAt",
    }:
        raise _ProxyFailure(502, "proxy.schema_drift")
    owners = payload.get("data")
    if not isinstance(owners, list) or len(owners) > 1_000:
        raise _ProxyFailure(502, "proxy.invalid_payload")
    sanitized_owners = []
    allowed = {
        "owner",
        "totalInstalls",
        "featuredRepo",
        "featuredSkill",
        "skills",
    }
    for owner in owners:
        if not isinstance(owner, Mapping) or set(owner) != allowed:
            raise _ProxyFailure(502, "proxy.schema_drift")
        sanitized_owners.append(
            {
                "owner": owner["owner"],
                "totalInstalls": owner["totalInstalls"],
                "featuredRepo": owner["featuredRepo"],
                "featuredSkill": owner["featuredSkill"],
                "skills": _sanitize_items(owner["skills"]),
            }
        )
    return {
        "data": sanitized_owners,
        "totalOwners": payload["totalOwners"],
        "totalSkills": payload["totalSkills"],
        "generatedAt": payload["generatedAt"],
    }


def _sanitize_audit(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "id",
        "source",
        "slug",
        "audits",
    }:
        raise _ProxyFailure(502, "proxy.schema_drift")
    audits = payload.get("audits")
    if not isinstance(audits, list) or len(audits) > SKILLS_PROXY_MAX_AUDITS:
        raise _ProxyFailure(502, "proxy.invalid_payload")
    allowed = {
        "provider",
        "slug",
        "status",
        "summary",
        "auditedAt",
        "riskLevel",
        "categories",
    }
    sanitized_audits = []
    for audit in audits:
        if not isinstance(audit, Mapping) or set(audit) - allowed:
            raise _ProxyFailure(502, "proxy.schema_drift")
        if audit.get("status") not in {"pass", "warn", "fail"}:
            raise _ProxyFailure(502, "proxy.invalid_payload")
        sanitized_audits.append(
            {
                key: audit[key]
                for key in ("provider", "slug", "status", "auditedAt", "riskLevel")
                if key in audit
            }
        )
    return {
        "id": payload["id"],
        "source": payload["source"],
        "slug": payload["slug"],
        "audits": sanitized_audits,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _validate_token(token: Any) -> None:
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 8_192
        or any(ord(char) < 33 for char in token)
    ):
        raise _ProxyFailure(503, "proxy.oidc_unavailable")


def _success_response(
    payload: dict[str, Any],
    encoded: bytes,
    *,
    max_age: int,
    cache_status: str,
    age_seconds: int,
    source_error: str | None = None,
) -> JSONResponse:
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
        "ETag": '"' + hashlib.sha256(encoded).hexdigest() + '"',
        "X-Content-Type-Options": "nosniff",
        "X-Giga-Cache-Status": cache_status,
        "Age": str(age_seconds),
    }
    if source_error is not None:
        headers["X-Giga-Source-Error"] = source_error
        headers["Warning"] = '110 - "Response is stale"'
    return JSONResponse(content=payload, headers=headers)


def _error_response(
    status_code: int,
    code: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": code},
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            **dict(headers or {}),
        },
    )
