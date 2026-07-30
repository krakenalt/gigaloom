from __future__ import annotations

import json

import pytest

from gigaloom.contracts import (
    CompactionBoundary,
    ContextDisposition,
    ContextEntryKind,
    ContextFreshness,
    ContextSourceDescriptor,
    InclusionReason,
    OmissionReason,
    ProtectedContextSourceError,
    ProviderManagedUnknown,
    TokenEstimateConfidence,
    TokenEstimateMethod,
    compile_context_lens,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


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
            source_id="file.changed",
            kind=ContextEntryKind.FILE,
            source_digest=SHA_B,
            disposition=ContextDisposition.INCLUDE,
            freshness=ContextFreshness.CURRENT,
            inclusion_reason=InclusionReason.USER_SELECTION,
            relative_path="src/gigaloom/example.py",
            token_count=80,
            token_method=TokenEstimateMethod.HEURISTIC,
            token_confidence=TokenEstimateConfidence.ESTIMATED,
        ),
        ContextSourceDescriptor(
            source_id="memory.previous",
            kind=ContextEntryKind.PROJECT_MEMORY,
            source_digest=SHA_C,
            disposition=ContextDisposition.OMIT,
            freshness=ContextFreshness.STALE,
            omission_reason=OmissionReason.STALE,
        ),
        ContextSourceDescriptor(
            source_id="tool.native",
            kind=ContextEntryKind.TOOL,
            source_digest=SHA_C,
            disposition=ContextDisposition.INCLUDE,
            freshness=ContextFreshness.UNKNOWN,
            inclusion_reason=InclusionReason.TOOL_AVAILABLE,
        ),
    )


def test_compiler_is_deterministic_and_every_source_is_visible() -> None:
    first = compile_context_lens(
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )
    second = compile_context_lens(
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=reversed(_sources()),
    )

    assert first == second
    assert {item.entry_id for item in first.manifest.entries} == {
        "instruction.repository",
        "file.changed",
        "tool.native",
    }
    assert [item.source_id for item in first.manifest.omissions] == ["memory.previous"]
    assert first.token_summary.known_token_count == 200
    assert first.token_summary.known_scope_count == 2
    assert first.token_summary.unknown_scope_count == 1
    assert first.is_partial is True


def test_protected_mandatory_instruction_cannot_be_omitted() -> None:
    with pytest.raises(
        ProtectedContextSourceError,
        match="protected instruction cannot be omitted",
    ):
        ContextSourceDescriptor(
            source_id="instruction.repository",
            kind=ContextEntryKind.INSTRUCTION,
            source_digest=SHA_A,
            disposition=ContextDisposition.OMIT,
            freshness=ContextFreshness.CURRENT,
            omission_reason=OmissionReason.EXCLUDED_BY_POLICY,
            protected=True,
        )


def test_stale_source_cannot_be_included() -> None:
    with pytest.raises(ValueError, match="stale source cannot be included"):
        ContextSourceDescriptor(
            source_id="file.stale",
            kind=ContextEntryKind.FILE,
            source_digest=SHA_A,
            disposition=ContextDisposition.INCLUDE,
            freshness=ContextFreshness.STALE,
            inclusion_reason=InclusionReason.USER_SELECTION,
        )


def test_duplicate_source_id_is_rejected_instead_of_hiding_an_omission() -> None:
    source = _sources()[0]
    with pytest.raises(ValueError, match="source ids must be unique"):
        compile_context_lens(
            source_revision="1" * 40,
            config_digest=SHA_A,
            sources=(source, source),
        )


def test_provider_context_remains_explicitly_unknown_and_content_free() -> None:
    projection = compile_context_lens(
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
        provider_managed_unknowns=(
            ProviderManagedUnknown(
                provider="codex",
                scope="native_context",
                reason="not_observable",
            ),
        ),
    )
    payload = projection.to_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert projection.is_partial is True
    assert "not_observable" in serialized
    assert "native_context" in serialized
    assert not {"content", "prompt", "text", "secret"} & set(payload["manifest"])


def test_compaction_is_a_linked_new_revision_not_a_complete_snapshot() -> None:
    before = compile_context_lens(
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=_sources(),
    )
    after = compile_context_lens(
        source_revision="2" * 40,
        config_digest=SHA_A,
        sources=_sources(),
        compaction_boundaries=(
            CompactionBoundary(
                boundary_id="compact_1",
                mode="native_only",
                source_manifest_digest=before.manifest.manifest_digest,
            ),
        ),
        provider_managed_unknowns=(
            ProviderManagedUnknown(
                provider="codex",
                scope="post_compaction_context",
                reason="not_observable",
            ),
        ),
    )

    assert after.manifest.manifest_digest != before.manifest.manifest_digest
    assert (
        after.manifest.compaction_boundaries[0].source_manifest_digest
        == before.manifest.manifest_digest
    )
    assert after.is_partial is True
