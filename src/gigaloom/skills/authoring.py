"""Deterministic portable Skill projection and package authoring."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

import yaml

from gigaloom.skills.portable_models import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    GeneratedSkillFile,
    GeneratedSkillPackage,
    PortableSkill,
    SkillCapabilitySnapshot,
    SkillMetadataDisposition,
    SkillMetadataReport,
    SkillTargetStatus,
    _SkillTargetContract,
    _normalize_relative_path,
    _target_contract,
    _validate_no_path_collisions,
)


def generate_skill_package(
    skill: PortableSkill,
    capability: SkillCapabilitySnapshot,
) -> GeneratedSkillPackage:
    """Generate one target package without dropping unsupported overlay fields."""
    contract = _target_contract(capability.target_id)
    generated, metadata = _project_skill_content(skill, contract)
    status = capability.status
    reason = capability.reason_code
    if status is SkillTargetStatus.SUPPORTED and not capability.supports_discovery:
        status = SkillTargetStatus.DEGRADED
        reason = "skill_discovery_not_supported"
    if status is SkillTargetStatus.SUPPORTED and not capability.supports_activation:
        status = SkillTargetStatus.DEGRADED
        reason = "skill_activation_not_supported"
    return GeneratedSkillPackage(
        target_id=contract.target_id,
        skill_name=skill.name,
        status=status,
        files=generated,
        metadata=metadata,
        activation_mode=contract.activation_mode,
        restart_required=contract.restart_required,
        reason_code=reason,
    )


def _project_skill_content(
    skill: PortableSkill,
    contract: _SkillTargetContract,
) -> tuple[tuple[GeneratedSkillFile, ...], tuple[SkillMetadataReport, ...]]:
    overlay = next(
        (item for item in skill.overlays if item.target_id == contract.target_id),
        None,
    )
    metadata: list[SkillMetadataReport] = []
    applied: dict[str, object] = {}
    if overlay is not None:
        for field in overlay.fields:
            if field.name in contract.metadata_fields:
                applied[field.name] = field.value
                disposition = SkillMetadataDisposition.APPLIED
                reason = None
            else:
                disposition = SkillMetadataDisposition.UNSUPPORTED
                reason = "target_metadata_unsupported"
            metadata.append(
                SkillMetadataReport(
                    target_id=contract.target_id,
                    field_name=field.name,
                    value_sha256=_metadata_hash(field.value),
                    disposition=disposition,
                    reason_code=reason,
                )
            )
    prefix = f"{contract.directory}/{skill.name}"
    core_metadata: dict[str, object] = {
        "name": skill.name,
        "description": skill.description,
    }
    if contract.target_id == CLAUDE_SKILL_TARGET_ID:
        core_metadata.update(applied)
    generated = [
        _generated_file(
            f"{prefix}/SKILL.md",
            _render_skill_md(core_metadata, skill.instructions),
            0o644,
        )
    ]
    generated.extend(
        _generated_file(f"{prefix}/{item.relative_path}", item.content, item.mode)
        for item in skill.files
    )
    if contract.target_id == CODEX_SKILL_TARGET_ID and applied:
        generated.append(
            _generated_file(
                f"{prefix}/agents/openai.yaml",
                _yaml_bytes(applied),
                0o644,
            )
        )
    projected_files = tuple(sorted(generated, key=lambda item: item.relative_path))
    _validate_no_path_collisions([item.relative_path for item in projected_files])
    return (
        projected_files,
        tuple(metadata),
    )


def portable_skill_semantic_hash(skill: PortableSkill) -> str:
    """Return a deterministic hash of the portable core and retained overlays."""
    payload = {
        "schema_version": skill.schema_version,
        "component_id": skill.component_id,
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "files": [
            {
                "relative_path": item.relative_path,
                "sha256": hashlib.sha256(item.content).hexdigest(),
                "mode": item.mode,
            }
            for item in skill.files
        ],
        "overlays": [
            {
                "target_id": overlay.target_id,
                "fields": [
                    {"name": field.name, "value": field.value}
                    for field in overlay.fields
                ],
            }
            for overlay in skill.overlays
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _generated_file(
    relative_path: str, content: bytes, mode: int
) -> GeneratedSkillFile:
    normalized = _normalize_relative_path(relative_path)
    return GeneratedSkillFile(
        relative_path=normalized,
        content=content,
        mode=mode,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _render_skill_md(metadata: Mapping[str, object], instructions: str) -> bytes:
    return (
        b"---\n"
        + _yaml_bytes(metadata)
        + b"---\n\n"
        + instructions.rstrip().encode("utf-8")
        + b"\n"
    )


def _yaml_bytes(value: Mapping[str, object]) -> bytes:
    rendered = yaml.safe_dump(
        dict(value),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    return rendered.encode("utf-8")


def _metadata_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
