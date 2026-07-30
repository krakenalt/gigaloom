"""Hermetic ingress and protected-sink adapters for the bounded 0.6 slice."""

from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from gigaloom.contracts import (
    InfluenceSet,
    ProvenanceClass,
    Sensitivity,
    SinkKind,
    SinkRequest,
    SourceRef,
    TrustClass,
)


_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20\x7f]")
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9a-fA-F]{2}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:authorization|api[-_]?key|access[-_]?token|password|secret|cookie)"
    r"\s*[:=]\s*[^\s,;]+"
)
_SECRET_TOKEN_RE = re.compile(
    r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/-]{8,}|\bsk-[A-Za-z0-9_-]{8,})"
)
_SECRET_METADATA_KEYS = frozenset(
    {
        "api-key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "proxy-authorization",
        "secret",
        "set-cookie",
        "token",
        "x-api-key",
    }
)
_EXTERNAL_WRITE_RE = re.compile(
    r"(?:"
    r"github://[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pulls)/[1-9][0-9]*"
    r"|mcp://[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/@+~-]+"
    r")\Z"
)


def user_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label exact user-authored content as the only initially trusted ingress."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.USER,
        trust=TrustClass.TRUSTED,
        sensitivity=sensitivity,
    )


def repo_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label repository content as bounded rather than authority-bearing."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.REPO,
        trust=TrustClass.BOUNDED,
        sensitivity=sensitivity,
    )


def web_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> SourceRef:
    """Label one Web result as bounded external influence."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.WEB,
        trust=TrustClass.BOUNDED,
        sensitivity=sensitivity,
    )


def mcp_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label one MCP result as bounded external influence."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.MCP,
        trust=TrustClass.BOUNDED,
        sensitivity=sensitivity,
    )


def attachment_source_ref(
    source_id: str,
    content_sha256: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label an attachment using the digest already verified at attachment ingress."""
    return SourceRef(
        source_id=source_id,
        provenance=ProvenanceClass.ATTACHMENT,
        trust=TrustClass.BOUNDED,
        sensitivity=sensitivity,
        content_sha256=content_sha256,
    )


def terminal_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label terminal output as influence that can never create authority."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.TERMINAL,
        trust=TrustClass.UNTRUSTED,
        sensitivity=sensitivity,
    )


def generated_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Label model-generated content as untrusted influence."""
    return _content_source_ref(
        source_id,
        content,
        provenance=ProvenanceClass.GENERATED,
        trust=TrustClass.UNTRUSTED,
        sensitivity=sensitivity,
    )


def legacy_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> SourceRef:
    """Migrate an old readable source without promoting unprovable provenance."""
    return SourceRef(
        source_id=source_id,
        provenance=None,
        trust=TrustClass.UNKNOWN,
        sensitivity=_detected_sensitivity(content, declared=sensitivity),
        content_sha256=payload_digest(content),
    )


def build_network_sink_request(
    *,
    request_id: str,
    url: str,
    payload: str | bytes,
    approved_destination_sha256: str,
    approved_payload_sha256: str,
    influence: InfluenceSet,
    destination_source_ids: tuple[str, ...],
    payload_source_ids: tuple[str, ...],
    headers: Mapping[str, str] | None = None,
    redirect_url: str | None = None,
    payload_sensitivity: Sensitivity = Sensitivity.INTERNAL,
    metadata_sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> SinkRequest:
    """Build a bounded network request without retaining raw payload or headers."""
    canonical_url = canonicalize_network_destination(url)
    destination_sha256 = _text_digest(canonical_url)
    effective_payload_sensitivity = _detected_sensitivity(
        payload,
        declared=payload_sensitivity,
    )
    effective_metadata_sensitivity = _metadata_sensitivity(
        url,
        headers=headers,
        declared=metadata_sensitivity,
    )
    redirect_sha256, redirect_requires_revalidation = _redirect_binding(
        canonical_url,
        redirect_url,
    )
    return SinkRequest(
        request_id=request_id,
        sink_kind=SinkKind.NETWORK_URL,
        destination_metadata=_network_destination_metadata(canonical_url),
        destination_sha256=destination_sha256,
        approved_destination_sha256=approved_destination_sha256,
        payload_sha256=payload_digest(payload),
        approved_payload_sha256=approved_payload_sha256,
        payload_preview=_payload_preview(
            payload,
            sensitivity=_max_sensitivity(
                effective_payload_sensitivity,
                effective_metadata_sensitivity,
            ),
        ),
        payload_sensitivity=effective_payload_sensitivity,
        metadata_sensitivity=effective_metadata_sensitivity,
        influence=influence,
        destination_source_ids=destination_source_ids,
        payload_source_ids=payload_source_ids,
        redirect_destination_sha256=redirect_sha256,
        redirect_requires_revalidation=redirect_requires_revalidation,
    )


def build_external_write_sink_request(
    *,
    request_id: str,
    destination: str,
    payload: str | bytes,
    approved_destination_sha256: str,
    approved_payload_sha256: str,
    influence: InfluenceSet,
    destination_source_ids: tuple[str, ...],
    payload_source_ids: tuple[str, ...],
    metadata: Mapping[str, str] | None = None,
    payload_sensitivity: Sensitivity = Sensitivity.INTERNAL,
    metadata_sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> SinkRequest:
    """Build a bounded GitHub-or-MCP write request without executing it."""
    canonical_destination = canonicalize_external_write_destination(destination)
    effective_payload_sensitivity = _detected_sensitivity(
        payload,
        declared=payload_sensitivity,
    )
    effective_metadata_sensitivity = _metadata_sensitivity(
        "",
        headers=metadata,
        declared=metadata_sensitivity,
    )
    return SinkRequest(
        request_id=request_id,
        sink_kind=SinkKind.EXTERNAL_WRITE,
        destination_metadata=canonical_destination,
        destination_sha256=_text_digest(canonical_destination),
        approved_destination_sha256=approved_destination_sha256,
        payload_sha256=payload_digest(payload),
        approved_payload_sha256=approved_payload_sha256,
        payload_preview=_payload_preview(
            payload,
            sensitivity=_max_sensitivity(
                effective_payload_sensitivity,
                effective_metadata_sensitivity,
            ),
        ),
        payload_sensitivity=effective_payload_sensitivity,
        metadata_sensitivity=effective_metadata_sensitivity,
        influence=influence,
        destination_source_ids=destination_source_ids,
        payload_source_ids=payload_source_ids,
    )


def canonicalize_network_destination(url: str) -> str:
    """Normalize one exact HTTP(S) destination or fail closed."""
    if not isinstance(url, str) or not url or _CONTROL_OR_SPACE_RE.search(url):
        raise ValueError("network destination is invalid")
    if "\\" in url:
        raise ValueError("network destination contains an ambiguous separator")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("network destination is invalid") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("network destination scheme is unsupported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("network destination userinfo is forbidden")
    if parsed.hostname is None:
        raise ValueError("network destination host is required")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("network destination host is invalid") from exc
    if ":" in host:
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    authority = host if port is None or default_port else f"{host}:{port}"
    path = _normalize_url_path(parsed.path)
    query = _normalize_percent_escapes(parsed.query)
    return urlunsplit((scheme, authority, path, query, ""))


def canonicalize_external_write_destination(destination: str) -> str:
    """Normalize one exact GitHub issue/PR or MCP tool write destination."""
    if (
        not isinstance(destination, str)
        or not destination
        or _CONTROL_OR_SPACE_RE.search(destination)
        or "\\" in destination
        or not _EXTERNAL_WRITE_RE.fullmatch(destination)
    ):
        raise ValueError("external write destination is invalid")
    scheme, remainder = destination.split("://", 1)
    owner, *path = remainder.split("/")
    if scheme == "github":
        repository = path[0]
        resource = path[1].lower()
        number = path[2]
        return f"github://{owner.lower()}/{repository.lower()}/{resource}/{number}"
    tool_path = "/".join(path)
    return f"mcp://{owner.lower()}/{tool_path}"


def network_destination_digest(url: str) -> str:
    """Return the exact normalized URL digest used by approval binding."""
    return _text_digest(canonicalize_network_destination(url))


def external_write_destination_digest(destination: str) -> str:
    """Return the normalized external-write digest used by approval binding."""
    return _text_digest(canonicalize_external_write_destination(destination))


def payload_digest(payload: str | bytes) -> str:
    """Hash the exact payload bytes without storing them."""
    return hashlib.sha256(_payload_bytes(payload)).hexdigest()


def _content_source_ref(
    source_id: str,
    content: str | bytes,
    *,
    provenance: ProvenanceClass,
    trust: TrustClass,
    sensitivity: Sensitivity,
) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        provenance=provenance,
        trust=trust,
        sensitivity=_detected_sensitivity(content, declared=sensitivity),
        content_sha256=payload_digest(content),
    )


def _redirect_binding(
    canonical_url: str,
    redirect_url: str | None,
) -> tuple[str | None, bool]:
    if redirect_url is None:
        return None, False
    try:
        canonical_redirect = canonicalize_network_destination(redirect_url)
    except ValueError:
        return None, True
    redirect_sha256 = _text_digest(canonical_redirect)
    return redirect_sha256, canonical_redirect != canonical_url


def _normalize_url_path(path: str) -> str:
    escaped = _normalize_percent_escapes(path or "/")
    trailing_slash = escaped.endswith("/")
    normalized = posixpath.normpath(escaped)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if trailing_slash and normalized != "/" and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def _normalize_percent_escapes(value: str) -> str:
    return _PERCENT_ESCAPE_RE.sub(lambda match: match.group(0).upper(), value)


def _network_destination_metadata(canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    query_keys = sorted({key for key, _value in parse_qsl(parsed.query)})
    if not query_keys:
        return base
    return f"{base}?query_keys={','.join(query_keys)}"


def _metadata_sensitivity(
    url: str,
    *,
    headers: Mapping[str, str] | None,
    declared: Sensitivity,
) -> Sensitivity:
    detected = declared
    if url:
        parsed = urlsplit(url)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if _is_secret_key(key) or _contains_secret(value):
                detected = Sensitivity.SECRET
                break
    for key, value in (headers or {}).items():
        if _is_secret_key(key) or _contains_secret(value):
            detected = Sensitivity.SECRET
            break
    return detected


def _detected_sensitivity(
    content: str | bytes,
    *,
    declared: Sensitivity,
) -> Sensitivity:
    if declared is Sensitivity.SECRET or _contains_secret(_payload_text(content)):
        return Sensitivity.SECRET
    return declared


def _contains_secret(value: str) -> bool:
    return bool(_SECRET_ASSIGNMENT_RE.search(value) or _SECRET_TOKEN_RE.search(value))


def _is_secret_key(value: str) -> bool:
    normalized = value.strip().lower().replace("_", "-")
    return normalized in _SECRET_METADATA_KEYS or normalized.endswith("-token")


def _payload_preview(payload: str | bytes, *, sensitivity: Sensitivity) -> str:
    if sensitivity is Sensitivity.SECRET:
        return ""
    if isinstance(payload, bytes):
        return "<binary payload>"
    return " ".join(payload.split())[:256]


def _payload_bytes(payload: str | bytes) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise ValueError("payload must be text or bytes")


def _payload_text(payload: str | bytes) -> str:
    return _payload_bytes(payload).decode("utf-8", errors="replace")


def _max_sensitivity(left: Sensitivity, right: Sensitivity) -> Sensitivity:
    rank = {
        Sensitivity.PUBLIC: 0,
        Sensitivity.INTERNAL: 1,
        Sensitivity.SECRET: 2,
    }
    return left if rank[left] >= rank[right] else right


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "attachment_source_ref",
    "build_external_write_sink_request",
    "build_network_sink_request",
    "canonicalize_external_write_destination",
    "canonicalize_network_destination",
    "external_write_destination_digest",
    "generated_source_ref",
    "legacy_source_ref",
    "mcp_source_ref",
    "network_destination_digest",
    "payload_digest",
    "repo_source_ref",
    "terminal_source_ref",
    "user_source_ref",
    "web_source_ref",
]
