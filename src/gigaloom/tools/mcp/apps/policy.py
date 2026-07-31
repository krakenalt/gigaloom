"""Static fail-closed policy for self-contained MCP App HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .contracts import (
    AdmittedMCPAppResource,
    MCPAppFallbackCode,
    MCPAppResourceCandidate,
)
from .errors import MCPAppAdmissionError

_BLOCKED_ELEMENTS = frozenset({"base", "embed", "frame", "iframe", "object", "portal"})
_REMOTE_RESOURCE_ATTRIBUTES = frozenset(
    {"action", "data", "formaction", "href", "poster", "src", "srcset"}
)
_CSS_RESOURCE_RE = re.compile(r"(?:@import\b|url\s*\()", re.IGNORECASE)


class _MCPAppHTMLPolicyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: set[str] = set()
        self._style_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}
        if normalized_tag in _BLOCKED_ELEMENTS:
            self.violations.add(f"blocked_element:{normalized_tag}")
        if normalized_tag == "meta" and "http-equiv" in attributes:
            self.violations.add("active_meta_directive")
        if normalized_tag == "style":
            self._style_depth += 1
        for name, value in attributes.items():
            if (
                name in _REMOTE_RESOURCE_ATTRIBUTES
                and value
                and not (name == "href" and value.startswith("#"))
            ):
                self.violations.add(f"resource_attribute:{normalized_tag}:{name}")
            if name == "style" and _CSS_RESOURCE_RE.search(value):
                self.violations.add("css_external_resource")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "style":
            self._style_depth = max(0, self._style_depth - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._style_depth = max(0, self._style_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._style_depth and _CSS_RESOURCE_RE.search(data):
            self.violations.add("css_external_resource")


def enforce_resource_policy(
    candidate: MCPAppResourceCandidate,
    resource: AdmittedMCPAppResource,
) -> tuple[str, ...]:
    """Reject active resources and all requested permissions/domains in v1."""
    parser = _MCPAppHTMLPolicyParser()
    parser.feed(resource.html.decode("utf-8"))
    parser.close()
    violations = set(parser.violations)
    if len(candidate.requested_permissions) > 32:
        violations.add("requested_permissions_limit")
    if len(candidate.requested_connect_domains) > 32:
        violations.add("requested_connect_domains_limit")
    violations.update(
        f"denied_permission:{_bounded_evidence(permission)}"
        for permission in candidate.requested_permissions[:32]
    )
    violations.update(
        f"denied_connect_domain:{_bounded_evidence(domain)}"
        for domain in candidate.requested_connect_domains[:32]
    )
    denied_evidence = tuple(sorted(violations))
    if denied_evidence:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.POLICY_DENIED.value,
            "MCP App resource requested capabilities denied by the v1 host policy",
            denied_evidence=denied_evidence,
        )
    return denied_evidence


def _bounded_evidence(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= 128:
        return value
    return encoded[:128].decode("utf-8", errors="ignore") + "..."
