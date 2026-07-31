from __future__ import annotations

import json
from dataclasses import replace

import pytest

from gigaloom.contracts import (
    CompactionBoundary,
    ContextDisposition,
    ContextEntry,
    ContextEntryKind,
    ContextFreshness,
    ContextManifestCache,
    ContextOmission,
    ContextOverride,
    ContextSourceDescriptor,
    InclusionReason,
    MAX_CONTEXT_ENTRIES,
    MAX_CONTEXT_SOURCES,
    OmissionReason,
    ProviderManagedUnknown,
    StaleContextManifestError,
    TokenEstimate,
    TokenEstimateConfidence,
    TokenEstimateMethod,
    build_context_manifest,
    compile_context_lens,
    context_manifest_from_dict,
    context_manifest_schema,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _manifest(*, source_revision: str = "1" * 40, config_digest: str = SHA_A):
    return build_context_manifest(
        source_revision=source_revision,
        config_digest=config_digest,
        entries=(
            ContextEntry(
                entry_id="tool.pytest",
                kind=ContextEntryKind.TOOL,
                source_digest=SHA_C,
                inclusion_reason=InclusionReason.TOOL_AVAILABLE,
            ),
            ContextEntry(
                entry_id="instruction.repository",
                kind=ContextEntryKind.INSTRUCTION,
                source_digest=SHA_B,
                inclusion_reason=InclusionReason.MANDATORY_INSTRUCTION,
                relative_path="AGENTS.md",
                size_bytes=2048,
            ),
        ),
        omissions=(
            ContextOmission(
                source_id="memory.optional",
                source_kind=ContextEntryKind.PROJECT_MEMORY,
                reason=OmissionReason.EXCLUDED_BY_POLICY,
                source_digest=SHA_C,
            ),
        ),
        overrides=(
            ContextOverride(
                key="model",
                value_digest=SHA_B,
                reason="operator_selection",
            ),
        ),
        compaction_boundaries=(
            CompactionBoundary(
                boundary_id="compact_1",
                mode="structured",
                upstream_event_id="item_1",
                source_manifest_digest=SHA_A,
            ),
        ),
        token_estimates=(
            TokenEstimate(
                scope_id="manifest",
                token_count=320,
                method=TokenEstimateMethod.TOKENIZER,
                confidence=TokenEstimateConfidence.ESTIMATED,
            ),
        ),
        provider_managed_unknowns=(
            ProviderManagedUnknown(
                provider="codex",
                scope="hidden_reasoning",
                reason="not_observable",
            ),
        ),
    )


def test_manifest_digest_is_deterministic_and_order_independent() -> None:
    first = _manifest()
    second = build_context_manifest(
        source_revision=first.source_revision,
        config_digest=first.config_digest,
        entries=tuple(reversed(first.entries)),
        omissions=first.omissions,
        overrides=first.overrides,
        compaction_boundaries=first.compaction_boundaries,
        token_estimates=first.token_estimates,
        provider_managed_unknowns=first.provider_managed_unknowns,
    )

    assert first == second
    assert first.manifest_id == f"ctxm_{first.manifest_digest[:24]}"
    assert len(first.manifest_digest) == 64
    assert context_manifest_from_dict(first.to_dict()) == first


def test_source_and_configuration_bind_the_manifest_and_cache() -> None:
    original = _manifest()
    changed_source = _manifest(source_revision="2" * 40)
    changed_config = _manifest(config_digest=SHA_B)

    assert (
        len(
            {
                original.manifest_digest,
                changed_source.manifest_digest,
                changed_config.manifest_digest,
            }
        )
        == 3
    )

    cache = ContextManifestCache(max_entries=2)
    cache.put("workspace_1", original)
    assert (
        cache.get(
            "workspace_1",
            source_revision=original.source_revision,
            config_digest=original.config_digest,
        )
        == original
    )
    with pytest.raises(StaleContextManifestError, match="source revision"):
        cache.get(
            "workspace_1",
            source_revision=changed_source.source_revision,
            config_digest=original.config_digest,
        )
    assert (
        cache.get(
            "workspace_1",
            source_revision=original.source_revision,
            config_digest=original.config_digest,
        )
        is None
    )
    cache.put("workspace_1", original)
    with pytest.raises(StaleContextManifestError, match="configuration digest"):
        cache.get(
            "workspace_1",
            source_revision=original.source_revision,
            config_digest=changed_config.config_digest,
        )


def test_cache_is_bounded_and_does_not_return_an_evicted_projection() -> None:
    cache = ContextManifestCache(max_entries=2)
    first = _manifest(source_revision="1" * 40)
    second = _manifest(source_revision="2" * 40)
    third = _manifest(source_revision="3" * 40)

    cache.put("first", first)
    cache.put("second", second)
    cache.get(
        "first",
        source_revision=first.source_revision,
        config_digest=first.config_digest,
    )
    cache.put("third", third)

    assert (
        cache.get(
            "second",
            source_revision=second.source_revision,
            config_digest=second.config_digest,
        )
        is None
    )
    assert len(cache) == 2


def test_manifest_parser_rejects_unknown_schema_and_digest_tampering() -> None:
    payload = _manifest().to_dict()

    with pytest.raises(ValueError, match="schema_version"):
        context_manifest_from_dict({**payload, "schema_version": 2})
    with pytest.raises(ValueError, match="manifest_digest"):
        context_manifest_from_dict({**payload, "manifest_digest": SHA_C})
    with pytest.raises(ValueError, match="unknown fields"):
        context_manifest_from_dict({**payload, "content": "must not persist"})


def test_wire_shape_is_content_free_and_paths_are_relative() -> None:
    payload = _manifest().to_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert not {"content", "prompt", "text", "secret"} & set(payload)
    assert "hidden_reasoning" in serialized
    assert "not_observable" in serialized
    with pytest.raises(ValueError, match="relative_path"):
        replace(_manifest().entries[0], relative_path="/tmp/x")


def test_schema_declares_digest_and_cache_binding_contracts() -> None:
    schema = context_manifest_schema()

    assert schema["schema_version"] == 1
    assert schema["format"] == "gigaloom.context-manifest.v1"
    assert schema["digest"] == "sha256-canonical-json-v1"
    assert schema["cache_binding"] == ["source_revision", "config_digest"]
    assert schema["content_free"] is True
    assert schema["freshness"] == ["current", "stale", "unknown"]
    assert schema["limits"]["entries"] == MAX_CONTEXT_ENTRIES


def test_manifest_and_lens_reject_unbounded_source_collections() -> None:
    entry = _manifest().entries[0]
    with pytest.raises(ValueError, match="entries exceeds"):
        build_context_manifest(
            source_revision="1" * 40,
            config_digest=SHA_A,
            entries=(entry,) * (MAX_CONTEXT_ENTRIES + 1),
        )

    payload = _manifest().to_dict()
    payload["entries"] = [entry.to_dict()] * (MAX_CONTEXT_ENTRIES + 1)
    with pytest.raises(ValueError, match="entries exceeds"):
        context_manifest_from_dict(payload)

    source = ContextSourceDescriptor(
        source_id="instruction.repository",
        kind=ContextEntryKind.INSTRUCTION,
        source_digest=SHA_A,
        disposition=ContextDisposition.INCLUDE,
        freshness=ContextFreshness.CURRENT,
        inclusion_reason=InclusionReason.MANDATORY_INSTRUCTION,
        protected=True,
    )
    with pytest.raises(ValueError, match="context sources exceed"):
        compile_context_lens(
            source_revision="1" * 40,
            config_digest=SHA_A,
            sources=(source,) * (MAX_CONTEXT_SOURCES + 1),
        )
