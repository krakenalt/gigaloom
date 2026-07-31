"""Admission contracts for one bounded local Visual QA target."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import unquote, urlsplit

from gigaloom.contracts import VisualViewportV1
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    validate_digest,
    validate_identity,
    validate_local_origin,
    validate_text,
)


DEFAULT_VISUAL_VIEWPORTS = (
    VisualViewportV1(viewport_id="desktop", width=1440, height=900),
    VisualViewportV1(viewport_id="mobile", width=390, height=844),
)
MAX_BROWSER_LIFETIME_MS = 120_000
MAX_BROWSER_REQUESTS = 512
MAX_REDIRECTS = 8

_SENSITIVE_URL_PATH = re.compile(
    r"(?:^|[-_./])(?:api[-_]?key|bearer|credential|password|secret|token)(?:[-_./]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class VisualProcessNetworkGrant:
    """Exact process and loopback network authority for a visual run."""

    grant_id: str
    process_id: int
    origin: str
    source_revision: str
    network_policy_digest: str
    exact_origin_only: bool = True

    def __post_init__(self) -> None:
        validate_identity(self.grant_id, field_name="visual grant id")
        if (
            isinstance(self.process_id, bool)
            or not isinstance(self.process_id, int)
            or not 1 <= self.process_id <= 2**31 - 1
        ):
            raise ValueError("visual process id is invalid")
        object.__setattr__(
            self,
            "origin",
            validate_local_origin(self.origin, field_name="visual grant origin"),
        )
        validate_identity(
            self.source_revision,
            field_name="visual grant source revision",
        )
        validate_digest(
            self.network_policy_digest,
            field_name="visual network policy digest",
        )
        if self.exact_origin_only is not True:
            raise ValueError("visual network grant must be exact-origin only")


@dataclass(frozen=True, slots=True)
class BrowserFingerprint:
    """Content-free identity for the isolated browser implementation."""

    engine: str
    browser_version: str
    executable_digest: str
    automation_name: str
    automation_version: str

    def __post_init__(self) -> None:
        validate_identity(self.engine, field_name="visual browser engine")
        validate_text(
            self.browser_version,
            field_name="visual browser version",
            max_chars=128,
        )
        validate_digest(
            self.executable_digest,
            field_name="visual browser executable digest",
        )
        validate_identity(
            self.automation_name,
            field_name="visual browser automation name",
        )
        validate_text(
            self.automation_version,
            field_name="visual browser automation version",
            max_chars=128,
        )

    @property
    def digest(self) -> str:
        """Return a stable browser/version/implementation fingerprint."""
        return canonical_digest(
            {
                "automation_name": self.automation_name,
                "automation_version": self.automation_version,
                "browser_version": self.browser_version,
                "engine": self.engine,
                "executable_digest": self.executable_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class VisualBrowserAdmission:
    """One admitted target with fixed viewports and runtime ceilings."""

    target_url: str
    grant: VisualProcessNetworkGrant
    browser: BrowserFingerprint
    viewports: tuple[VisualViewportV1, ...] = DEFAULT_VISUAL_VIEWPORTS
    browser_lifetime_ms: int = 30_000
    max_requests: int = 128
    max_redirects: int = 4
    fresh_profile: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.grant, VisualProcessNetworkGrant):
            raise ValueError("visual process/network grant is invalid")
        if not isinstance(self.browser, BrowserFingerprint):
            raise ValueError("visual browser fingerprint is invalid")
        _validate_secret_free_target_url(self.target_url)
        if _url_origin(self.target_url) != self.grant.origin:
            raise ValueError("visual target URL exceeds the exact origin grant")
        if self.viewports != DEFAULT_VISUAL_VIEWPORTS:
            raise ValueError("visual admission requires desktop and 390x844 viewports")
        for value, upper, field_name in (
            (
                self.browser_lifetime_ms,
                MAX_BROWSER_LIFETIME_MS,
                "visual browser lifetime",
            ),
            (self.max_requests, MAX_BROWSER_REQUESTS, "visual browser requests"),
            (self.max_redirects, MAX_REDIRECTS, "visual browser redirects"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= upper
            ):
                raise ValueError(f"{field_name} limit is invalid")
        if self.fresh_profile is not True:
            raise ValueError("visual browser profile reuse is forbidden")

    @property
    def origin_policy_digest(self) -> str:
        """Bind the receipt to the exact process/network/browser admission."""
        return canonical_digest(
            {
                "browser_lifetime_ms": self.browser_lifetime_ms,
                "exact_origin_only": self.grant.exact_origin_only,
                "fresh_profile": self.fresh_profile,
                "grant_id": self.grant.grant_id,
                "max_redirects": self.max_redirects,
                "max_requests": self.max_requests,
                "network_policy_digest": self.grant.network_policy_digest,
                "origin": self.grant.origin,
                "process_id": self.grant.process_id,
                "source_revision": self.grant.source_revision,
                "viewports": [
                    {
                        "device_scale_factor": item.device_scale_factor,
                        "height": item.height,
                        "viewport_id": item.viewport_id,
                        "width": item.width,
                    }
                    for item in self.viewports
                ],
            }
        )


def _validate_secret_free_target_url(value: object) -> str:
    text = validate_text(value, field_name="visual target URL", max_chars=2_048)
    parsed = urlsplit(text)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("visual target URL has an invalid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _SENSITIVE_URL_PATH.search(unquote(parsed.path)) is not None
    ):
        raise ValueError("visual target URL must be secret-free and loopback-only")
    return text


def _url_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    rendered_host = f"[{host}]" if ":" in host else host
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{rendered_host}{port}"


__all__ = [
    "DEFAULT_VISUAL_VIEWPORTS",
    "MAX_BROWSER_LIFETIME_MS",
    "MAX_BROWSER_REQUESTS",
    "MAX_REDIRECTS",
    "BrowserFingerprint",
    "VisualBrowserAdmission",
    "VisualProcessNetworkGrant",
]
