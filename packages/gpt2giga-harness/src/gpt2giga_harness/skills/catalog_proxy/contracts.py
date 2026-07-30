"""Skills catalog proxy limits, settings, and injected transport contracts."""

from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import os
import re
from threading import Lock
from typing import Any, Protocol

SKILLS_PROXY_UPSTREAM_ORIGIN = "https://skills.sh"
SKILLS_PROXY_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SKILLS_PROXY_TIMEOUT_SECONDS = 20.0
SKILLS_PROXY_MAX_PAGE_SIZE = 500
SKILLS_PROXY_MAX_SEARCH_LIMIT = 200
SKILLS_PROXY_MAX_QUERY_LENGTH = 200
SKILLS_PROXY_MAX_CACHE_ENTRIES = 128
SKILLS_PROXY_STALE_IF_ERROR_SECONDS = 3_600
SKILLS_PROXY_MAX_FILE_PATHS = 512
SKILLS_PROXY_MAX_AUDITS = 32
_PATH_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_LIST_ITEM_FIELDS = {
    "id",
    "slug",
    "name",
    "source",
    "installs",
    "sourceType",
    "installUrl",
    "url",
    "isDuplicate",
    "installsYesterday",
    "change",
}


@dataclass(frozen=True)
class SkillsCatalogProxySettings:
    """Safe runtime settings; the upstream origin is intentionally not configurable."""

    listen_host: str = "127.0.0.1"
    listen_port: int = 8092
    rate_limit_per_minute: int = 120
    max_cache_entries: int = SKILLS_PROXY_MAX_CACHE_ENTRIES
    stale_if_error_seconds: int = SKILLS_PROXY_STALE_IF_ERROR_SECONDS

    def __post_init__(self) -> None:
        if self.listen_host not in {"127.0.0.1", "0.0.0.0", "::1"}:
            raise ValueError("skills proxy listen host is invalid")
        if (
            isinstance(self.listen_port, bool)
            or not isinstance(self.listen_port, int)
            or not 1 <= self.listen_port <= 65_535
        ):
            raise ValueError("skills proxy listen port is invalid")
        if (
            isinstance(self.rate_limit_per_minute, bool)
            or not isinstance(self.rate_limit_per_minute, int)
            or not 1 <= self.rate_limit_per_minute <= 600
        ):
            raise ValueError("skills proxy rate limit is invalid")
        if (
            isinstance(self.max_cache_entries, bool)
            or not isinstance(self.max_cache_entries, int)
            or not 1 <= self.max_cache_entries <= 1_024
        ):
            raise ValueError("skills proxy cache bound is invalid")
        if (
            isinstance(self.stale_if_error_seconds, bool)
            or not isinstance(self.stale_if_error_seconds, int)
            or not 60 <= self.stale_if_error_seconds <= 86_400
        ):
            raise ValueError("skills proxy stale window is invalid")

    @classmethod
    def from_env(cls) -> SkillsCatalogProxySettings:
        """Read only bounded listener settings from the process environment."""
        return cls(
            listen_host=os.environ.get("GIGA_SKILLS_PROXY_HOST", "127.0.0.1"),
            listen_port=_env_integer("GIGA_SKILLS_PROXY_PORT", 8092),
            rate_limit_per_minute=_env_integer("GIGA_SKILLS_PROXY_RATE_LIMIT", 120),
            max_cache_entries=_env_integer(
                "GIGA_SKILLS_PROXY_CACHE_ENTRIES",
                SKILLS_PROXY_MAX_CACHE_ENTRIES,
            ),
            stale_if_error_seconds=_env_integer(
                "GIGA_SKILLS_PROXY_STALE_IF_ERROR_SECONDS",
                SKILLS_PROXY_STALE_IF_ERROR_SECONDS,
            ),
        )


@dataclass(frozen=True)
class SkillsProxyUpstreamResponse:
    """Bounded response returned by an injected upstream transport."""

    status_code: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes
    redirected: bool = False


SkillsOIDCTokenProvider = Callable[[], Awaitable[str]]


class SkillsProxyUpstreamTransport(Protocol):
    """Injectable transport used by hermetic proxy tests."""

    async def __call__(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> SkillsProxyUpstreamResponse: ...


class _RateLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def admit(self, identity: str, now: float) -> bool:
        with self._lock:
            hits = self._hits[identity]
            cutoff = now - 60.0
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


@dataclass(frozen=True)
class _CacheEntry:
    payload: dict[str, Any]
    encoded: bytes
    stored_at: float
    max_age: int


class _LastGoodCache:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> _CacheEntry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, entry: _CacheEntry) -> None:
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._maximum:
                self._entries.popitem(last=False)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


def _env_integer(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
