from __future__ import annotations

import json

import pytest

from gigaloom.contracts import (
    ContextDisposition,
    ContextEntryKind,
    ContextFreshness,
    ContextSourceDescriptor,
    InclusionReason,
    TokenEstimateConfidence,
    TokenEstimateMethod,
)
from gigaloom.execution.api import (
    NativeCodexCompactionObservation,
    NativeCodexContextMode,
    compile_native_codex_context,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _sources() -> tuple[ContextSourceDescriptor, ...]:
    return (
        ContextSourceDescriptor(
            source_id="instruction.repository",
            kind=ContextEntryKind.INSTRUCTION,
            source_digest=SHA_A,
            disposition=ContextDisposition.INCLUDE,
            freshness=ContextFreshness.CURRENT,
            inclusion_reason=InclusionReason.MANDATORY_INSTRUCTION,
            relative_path="AGENTS.md",
            protected=True,
            token_count=120,
            token_method=TokenEstimateMethod.TOKENIZER,
            token_confidence=TokenEstimateConfidence.ESTIMATED,
        ),
        ContextSourceDescriptor(
            source_id="tool.mcp",
            kind=ContextEntryKind.TOOL,
            source_digest=SHA_B,
            disposition=ContextDisposition.INCLUDE,
            freshness=ContextFreshness.CURRENT,
            inclusion_reason=InclusionReason.TOOL_AVAILABLE,
        ),
    )


def test_structured_codex_projection_binds_session_without_exposing_thread_id() -> None:
    projection = compile_native_codex_context(
        harness_session_id="session_1",
        native_thread_id="thread-secret-1",
        mode=NativeCodexContextMode.STRUCTURED,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )

    payload = projection.to_dict()
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["binding"]["harness_session_id"] == "session_1"
    assert payload["binding"]["native_thread_digest"] is not None
    assert payload["binding"]["manifest_digest"] == (
        projection.lens.manifest.manifest_digest
    )
    assert "thread-secret-1" not in serialized
    assert {
        (item.scope, item.reason)
        for item in projection.lens.manifest.provider_managed_unknowns
    } == {
        ("hidden_reasoning", "not_observable"),
        ("native_context", "provider_managed"),
    }


def test_structured_compaction_creates_a_linked_new_manifest_revision() -> None:
    before = compile_native_codex_context(
        harness_session_id="session_1",
        native_thread_id="thread-1",
        mode=NativeCodexContextMode.STRUCTURED,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )
    after = compile_native_codex_context(
        harness_session_id="session_1",
        native_thread_id="thread-1",
        mode=NativeCodexContextMode.STRUCTURED,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
        compaction=NativeCodexCompactionObservation(
            boundary_id="compact_1",
            source_manifest_digest=before.lens.manifest.manifest_digest,
            upstream_event_id="item_1",
        ),
        previous_manifest=before.lens.manifest,
    )

    boundary = after.lens.manifest.compaction_boundaries[0]
    assert after.lens.manifest.manifest_digest != before.lens.manifest.manifest_digest
    assert boundary.mode == "structured"
    assert boundary.source_manifest_digest == before.lens.manifest.manifest_digest
    assert boundary.upstream_event_id == "item_1"
    assert after.binding.previous_manifest_digest == (
        before.lens.manifest.manifest_digest
    )


def test_compaction_requires_exact_previous_manifest_and_structured_event() -> None:
    before = compile_native_codex_context(
        harness_session_id="session_1",
        native_thread_id="thread-1",
        mode=NativeCodexContextMode.STRUCTURED,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )

    with pytest.raises(ValueError, match="upstream event"):
        NativeCodexCompactionObservation(
            boundary_id="compact_1",
            source_manifest_digest=before.lens.manifest.manifest_digest,
        )
    with pytest.raises(ValueError, match="previous manifest"):
        compile_native_codex_context(
            harness_session_id="session_1",
            native_thread_id="thread-1",
            mode=NativeCodexContextMode.STRUCTURED,
            source_revision="1" * 40,
            config_digest=SHA_A,
            sources=_sources(),
            compaction=NativeCodexCompactionObservation(
                boundary_id="compact_1",
                source_manifest_digest=SHA_B,
                upstream_event_id="item_1",
            ),
            previous_manifest=before.lens.manifest,
        )


def test_native_only_projection_never_invents_thread_or_compaction_evidence() -> None:
    projection = compile_native_codex_context(
        harness_session_id="session_1",
        native_thread_id=None,
        mode=NativeCodexContextMode.NATIVE_ONLY,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )

    assert projection.binding.native_thread_digest is None
    assert projection.lens.manifest.compaction_boundaries == ()
    with pytest.raises(ValueError, match="structured mode"):
        compile_native_codex_context(
            harness_session_id="session_1",
            native_thread_id=None,
            mode=NativeCodexContextMode.NATIVE_ONLY,
            source_revision="1" * 40,
            config_digest=SHA_A,
            sources=_sources(),
            compaction=NativeCodexCompactionObservation(
                boundary_id="compact_1",
                source_manifest_digest=projection.lens.manifest.manifest_digest,
                upstream_event_id="item_1",
            ),
            previous_manifest=projection.lens.manifest,
        )


def test_structured_projection_requires_an_exact_native_thread_binding() -> None:
    with pytest.raises(ValueError, match="native thread"):
        compile_native_codex_context(
            harness_session_id="session_1",
            native_thread_id=None,
            mode=NativeCodexContextMode.STRUCTURED,
            source_revision="1" * 40,
            config_digest=SHA_A,
            sources=_sources(),
        )
