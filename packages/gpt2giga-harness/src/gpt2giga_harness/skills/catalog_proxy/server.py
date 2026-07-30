"""Read-only fixed-origin proxy for authenticated skills.sh metadata."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from threading import Lock
import time
from typing import Any
from urllib import parse as urllib_parse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gpt2giga_harness.skills.catalog_proxy.contracts import (
    SKILLS_PROXY_MAX_PAGE_SIZE,
    SKILLS_PROXY_MAX_QUERY_LENGTH,
    SKILLS_PROXY_MAX_RESPONSE_BYTES,
    SKILLS_PROXY_MAX_SEARCH_LIMIT,
    SKILLS_PROXY_TIMEOUT_SECONDS,
    SKILLS_PROXY_UPSTREAM_ORIGIN,
    SkillsCatalogProxySettings,
    SkillsOIDCTokenProvider,
    SkillsProxyUpstreamResponse,
    SkillsProxyUpstreamTransport,
    _CacheEntry,
    _LastGoodCache,
    _PATH_PART_RE,
    _RateLimiter,
)
from gpt2giga_harness.skills.catalog_proxy.transport import (
    _ProxyFailure,
    _environment_oidc_token,
    fetch_skills_proxy_upstream,
)
from gpt2giga_harness.skills.catalog_proxy.validation import (
    _error_response,
    _sanitize_audit,
    _sanitize_curated,
    _sanitize_detail,
    _sanitize_listing,
    _success_response,
    _validate_token,
    _validated_upstream_payload,
)


def create_skills_catalog_proxy_app(
    *,
    settings: SkillsCatalogProxySettings | None = None,
    token_provider: SkillsOIDCTokenProvider | None = None,
    upstream: SkillsProxyUpstreamTransport | None = None,
    monotonic: Callable[[], float] | None = None,
) -> FastAPI:
    """Create the independently deployable metadata-only proxy application."""
    config = settings or SkillsCatalogProxySettings.from_env()
    resolve_token = token_provider or _environment_oidc_token
    transport = upstream or fetch_skills_proxy_upstream
    limiter = _RateLimiter(config.rate_limit_per_minute)
    cache = _LastGoodCache(config.max_cache_entries)
    clock = monotonic or time.monotonic
    health_state: dict[str, str | None] = {
        "last_error_code": None,
        "last_success_path": None,
    }
    health_lock = Lock()
    application = FastAPI(
        title="Harness skills.sh metadata proxy",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path != "/healthz":
            identity = request.client.host if request.client is not None else "unknown"
            if not limiter.admit(identity, clock()):
                return _error_response(429, "proxy.rate_limited")
        return await call_next(request)

    @application.get("/healthz")
    async def _health() -> dict[str, Any]:
        with health_lock:
            last_error_code = health_state["last_error_code"]
            last_success_path = health_state["last_success_path"]
        oidc_configured = token_provider is not None or bool(
            os.environ.get("VERCEL_OIDC_TOKEN")
        )
        return {
            "status": "ready" if oidc_configured else "configuration_required",
            "upstream_origin": SKILLS_PROXY_UPSTREAM_ORIGIN,
            "read_only": True,
            "oidc_configured": oidc_configured,
            "cache_entries": cache.count(),
            "last_good_available": cache.count() > 0,
            "last_error_code": last_error_code,
            "last_success_path": last_success_path,
        }

    async def _proxy(
        url: str,
        *,
        sanitizer: Callable[[Any], dict[str, Any]],
        max_age: int,
    ) -> JSONResponse:
        try:
            token = await resolve_token()
            _validate_token(token)
            response = await transport(
                url=url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout_seconds=SKILLS_PROXY_TIMEOUT_SECONDS,
                max_response_bytes=SKILLS_PROXY_MAX_RESPONSE_BYTES,
            )
            payload = _validated_upstream_payload(response)
            sanitized = sanitizer(payload)
        except _ProxyFailure as exc:
            with health_lock:
                health_state["last_error_code"] = exc.code
            retained = cache.get(url)
            age = clock() - retained.stored_at if retained is not None else None
            if (
                retained is not None
                and age is not None
                and 0 <= age <= config.stale_if_error_seconds
            ):
                return _success_response(
                    retained.payload,
                    retained.encoded,
                    max_age=0,
                    cache_status="stale",
                    age_seconds=int(age),
                    source_error=exc.code,
                )
            return _error_response(exc.status_code, exc.code, headers=exc.headers)
        except Exception:
            with health_lock:
                health_state["last_error_code"] = "proxy.upstream_unavailable"
            retained = cache.get(url)
            age = clock() - retained.stored_at if retained is not None else None
            if (
                retained is not None
                and age is not None
                and 0 <= age <= config.stale_if_error_seconds
            ):
                return _success_response(
                    retained.payload,
                    retained.encoded,
                    max_age=0,
                    cache_status="stale",
                    age_seconds=int(age),
                    source_error="proxy.upstream_unavailable",
                )
            return _error_response(502, "proxy.upstream_unavailable")
        encoded = json.dumps(
            sanitized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(encoded) > SKILLS_PROXY_MAX_RESPONSE_BYTES:
            return _error_response(502, "proxy.response_too_large")
        cache.put(
            url,
            _CacheEntry(
                payload=sanitized,
                encoded=encoded,
                stored_at=clock(),
                max_age=max_age,
            ),
        )
        with health_lock:
            health_state["last_error_code"] = None
            health_state["last_success_path"] = urllib_parse.urlsplit(url).path
        return _success_response(
            sanitized,
            encoded,
            max_age=max_age,
            cache_status="fresh",
            age_seconds=0,
        )

    @application.get("/api/v1/skills")
    async def _list_skills(
        view: str = "all-time",
        page: int = 0,
        per_page: int = 100,
    ) -> JSONResponse:
        if view not in {"all-time", "trending", "hot"}:
            return _error_response(400, "proxy.invalid_view")
        if page < 0 or not 1 <= per_page <= SKILLS_PROXY_MAX_PAGE_SIZE:
            return _error_response(400, "proxy.invalid_pagination")
        query = urllib_parse.urlencode(
            {"view": view, "page": page, "per_page": per_page}
        )
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills?{query}",
            sanitizer=_sanitize_listing,
            max_age=60,
        )

    @application.get("/api/v1/skills/search")
    async def _search_skills(
        q: str,
        limit: int = 50,
        owner: str | None = None,
    ) -> JSONResponse:
        if not 2 <= len(q.strip()) <= SKILLS_PROXY_MAX_QUERY_LENGTH:
            return _error_response(400, "proxy.invalid_query")
        if not 1 <= limit <= SKILLS_PROXY_MAX_SEARCH_LIMIT:
            return _error_response(400, "proxy.invalid_limit")
        if owner is not None and _PATH_PART_RE.fullmatch(owner) is None:
            return _error_response(400, "proxy.invalid_owner")
        params: dict[str, str | int] = {"q": q.strip(), "limit": limit}
        if owner is not None:
            params["owner"] = owner
        query = urllib_parse.urlencode(params)
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/search?{query}",
            sanitizer=_sanitize_listing,
            max_age=60,
        )

    @application.get("/api/v1/skills/curated")
    async def _curated_skills() -> JSONResponse:
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/curated",
            sanitizer=_sanitize_curated,
            max_age=300,
        )

    @application.get("/api/v1/skills/audit/{owner}/{repository}/{skill}")
    async def _github_audit(
        owner: str,
        repository: str,
        skill: str,
    ) -> JSONResponse:
        if not all(
            _PATH_PART_RE.fullmatch(item) for item in (owner, repository, skill)
        ):
            return _error_response(400, "proxy.invalid_skill_id")
        path = "/".join(
            urllib_parse.quote(item, safe="") for item in (owner, repository, skill)
        )
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/audit/{path}",
            sanitizer=_sanitize_audit,
            max_age=300,
        )

    @application.get("/api/v1/skills/audit/{source}/{skill}")
    async def _well_known_audit(source: str, skill: str) -> JSONResponse:
        if not all(_PATH_PART_RE.fullmatch(item) for item in (source, skill)):
            return _error_response(400, "proxy.invalid_skill_id")
        path = "/".join(urllib_parse.quote(item, safe="") for item in (source, skill))
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/audit/{path}",
            sanitizer=_sanitize_audit,
            max_age=300,
        )

    @application.get("/api/v1/skills/{owner}/{repository}/{skill}")
    async def _github_detail(
        owner: str,
        repository: str,
        skill: str,
    ) -> JSONResponse:
        if not all(
            _PATH_PART_RE.fullmatch(item) for item in (owner, repository, skill)
        ):
            return _error_response(400, "proxy.invalid_skill_id")
        path = "/".join(
            urllib_parse.quote(item, safe="") for item in (owner, repository, skill)
        )
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/{path}",
            sanitizer=_sanitize_detail,
            max_age=300,
        )

    @application.get("/api/v1/skills/{source}/{skill}")
    async def _well_known_detail(source: str, skill: str) -> JSONResponse:
        if not all(_PATH_PART_RE.fullmatch(item) for item in (source, skill)):
            return _error_response(400, "proxy.invalid_skill_id")
        path = "/".join(urllib_parse.quote(item, safe="") for item in (source, skill))
        return await _proxy(
            f"{SKILLS_PROXY_UPSTREAM_ORIGIN}/api/v1/skills/{path}",
            sanitizer=_sanitize_detail,
            max_age=300,
        )

    return application


def main() -> None:
    """Run the packaged proxy entry point."""
    import uvicorn

    settings = SkillsCatalogProxySettings.from_env()
    uvicorn.run(
        "gpt2giga_harness.skills_catalog_proxy:app",
        host=settings.listen_host,
        port=settings.listen_port,
        access_log=False,
    )


app = create_skills_catalog_proxy_app()


__all__ = [
    "SKILLS_PROXY_MAX_RESPONSE_BYTES",
    "SKILLS_PROXY_UPSTREAM_ORIGIN",
    "SkillsCatalogProxySettings",
    "SkillsProxyUpstreamResponse",
    "SkillsProxyUpstreamTransport",
    "app",
    "create_skills_catalog_proxy_app",
    "fetch_skills_proxy_upstream",
    "main",
]
