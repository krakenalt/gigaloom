"""Content-free native Codex Context Lens application projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable

from gigaloom.contracts import (
    CompactionBoundary,
    ContextLensProjection,
    ContextManifest,
    ContextOverride,
    ContextSourceDescriptor,
    ProviderManagedUnknown,
    compile_context_lens,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class NativeCodexContextMode(str, Enum):
    """Evidence level available for one native Codex session."""

    STRUCTURED = "structured"
    NATIVE_ONLY = "native_only"


@dataclass(frozen=True)
class NativeCodexCompactionObservation:
    """Completed structured compaction evidence emitted by Codex app-server."""

    boundary_id: str
    source_manifest_digest: str
    upstream_event_id: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.boundary_id, "boundary_id")
        _validate_sha256(self.source_manifest_digest, "source_manifest_digest")
        if self.upstream_event_id is None:
            raise ValueError("structured compaction requires an upstream event id")
        _validate_text(self.upstream_event_id, "upstream event id")


@dataclass(frozen=True)
class NativeCodexContextBinding:
    """Digest-only binding between one Harness session and one manifest."""

    harness_session_id: str
    native_thread_digest: str | None
    mode: NativeCodexContextMode
    manifest_id: str
    manifest_digest: str
    source_revision: str
    config_digest: str
    previous_manifest_digest: str | None
    binding_digest: str

    def __post_init__(self) -> None:
        _validate_text(self.harness_session_id, "harness_session_id")
        if self.native_thread_digest is not None:
            _validate_sha256(self.native_thread_digest, "native_thread_digest")
        if self.mode is NativeCodexContextMode.STRUCTURED:
            if self.native_thread_digest is None:
                raise ValueError("structured context requires a native thread binding")
        elif self.native_thread_digest is not None:
            raise ValueError("native_only context cannot claim a native thread binding")
        _validate_text(self.manifest_id, "manifest_id")
        _validate_sha256(self.manifest_digest, "manifest_digest")
        _validate_text(self.source_revision, "source_revision")
        _validate_sha256(self.config_digest, "config_digest")
        if self.previous_manifest_digest is not None:
            _validate_sha256(
                self.previous_manifest_digest,
                "previous_manifest_digest",
            )
        _validate_sha256(self.binding_digest, "binding_digest")
        if self.binding_digest != _digest(self._digest_payload()):
            raise ValueError("binding_digest does not match binding payload")

    def _digest_payload(self) -> dict[str, Any]:
        return {
            "harness_session_id": self.harness_session_id,
            "native_thread_digest": self.native_thread_digest,
            "mode": self.mode.value,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest,
            "source_revision": self.source_revision,
            "config_digest": self.config_digest,
            "previous_manifest_digest": self.previous_manifest_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the digest-only public representation."""
        return {**self._digest_payload(), "binding_digest": self.binding_digest}


@dataclass(frozen=True)
class NativeCodexContextProjection:
    """One Context Lens plus its exact native session binding."""

    binding: NativeCodexContextBinding
    lens: ContextLensProjection

    def __post_init__(self) -> None:
        manifest = self.lens.manifest
        if (
            self.binding.manifest_id != manifest.manifest_id
            or self.binding.manifest_digest != manifest.manifest_digest
            or self.binding.source_revision != manifest.source_revision
            or self.binding.config_digest != manifest.config_digest
        ):
            raise ValueError("native binding does not match ContextManifest")

    def to_dict(self) -> dict[str, Any]:
        """Return content-free session and Context Lens evidence."""
        return {
            "binding": self.binding.to_dict(),
            "lens": self.lens.to_dict(),
        }


def compile_native_codex_context(
    *,
    harness_session_id: str,
    native_thread_id: str | None,
    mode: NativeCodexContextMode,
    source_revision: str,
    config_digest: str,
    sources: Iterable[ContextSourceDescriptor],
    overrides: Iterable[ContextOverride] = (),
    compaction: NativeCodexCompactionObservation | None = None,
    previous_manifest: ContextManifest | None = None,
) -> NativeCodexContextProjection:
    """Compile only GigaLoom-observable Codex context and binding evidence."""
    if not isinstance(mode, NativeCodexContextMode):
        raise ValueError("unsupported native Codex context mode")
    _validate_text(harness_session_id, "harness_session_id")
    _validate_text(source_revision, "source_revision")
    _validate_sha256(config_digest, "config_digest")
    thread_digest = _native_thread_digest(native_thread_id, mode=mode)
    boundary, previous_digest = _compaction_boundary(
        mode=mode,
        compaction=compaction,
        previous_manifest=previous_manifest,
    )
    lens = compile_context_lens(
        source_revision=source_revision,
        config_digest=config_digest,
        sources=sources,
        overrides=overrides,
        compaction_boundaries=(() if boundary is None else (boundary,)),
        provider_managed_unknowns=(
            ProviderManagedUnknown(
                provider="codex",
                scope="hidden_reasoning",
                reason="not_observable",
            ),
            ProviderManagedUnknown(
                provider="codex",
                scope="native_context",
                reason="provider_managed",
            ),
        ),
    )
    digest_payload = {
        "harness_session_id": harness_session_id,
        "native_thread_digest": thread_digest,
        "mode": mode.value,
        "manifest_id": lens.manifest.manifest_id,
        "manifest_digest": lens.manifest.manifest_digest,
        "source_revision": lens.manifest.source_revision,
        "config_digest": lens.manifest.config_digest,
        "previous_manifest_digest": previous_digest,
    }
    binding = NativeCodexContextBinding(
        harness_session_id=harness_session_id,
        native_thread_digest=thread_digest,
        mode=mode,
        manifest_id=lens.manifest.manifest_id,
        manifest_digest=lens.manifest.manifest_digest,
        source_revision=lens.manifest.source_revision,
        config_digest=lens.manifest.config_digest,
        previous_manifest_digest=previous_digest,
        binding_digest=_digest(digest_payload),
    )
    return NativeCodexContextProjection(binding=binding, lens=lens)


def _native_thread_digest(
    native_thread_id: str | None,
    *,
    mode: NativeCodexContextMode,
) -> str | None:
    if mode is NativeCodexContextMode.STRUCTURED:
        if native_thread_id is None:
            raise ValueError("structured context requires an exact native thread id")
        _validate_text(native_thread_id, "native_thread_id")
        return hashlib.sha256(native_thread_id.encode("utf-8")).hexdigest()
    if native_thread_id is not None:
        raise ValueError("native_only context cannot claim a native thread id")
    return None


def _compaction_boundary(
    *,
    mode: NativeCodexContextMode,
    compaction: NativeCodexCompactionObservation | None,
    previous_manifest: ContextManifest | None,
) -> tuple[CompactionBoundary | None, str | None]:
    if compaction is None:
        if previous_manifest is not None:
            raise ValueError("previous manifest requires a compaction observation")
        return None, None
    if mode is not NativeCodexContextMode.STRUCTURED:
        raise ValueError("compaction evidence requires structured mode")
    if previous_manifest is None:
        raise ValueError("compaction evidence requires the previous manifest")
    if compaction.source_manifest_digest != previous_manifest.manifest_digest:
        raise ValueError("compaction source does not match the previous manifest")
    assert compaction.upstream_event_id is not None
    return (
        CompactionBoundary(
            boundary_id=compaction.boundary_id,
            mode=mode.value,
            source_manifest_digest=compaction.source_manifest_digest,
            upstream_event_id=compaction.upstream_event_id,
        ),
        previous_manifest.manifest_digest,
    )


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{field_name} must be 1..256 characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{field_name} contains forbidden control characters")


def _validate_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


__all__ = [
    "NativeCodexCompactionObservation",
    "NativeCodexContextBinding",
    "NativeCodexContextMode",
    "NativeCodexContextProjection",
    "compile_native_codex_context",
]
