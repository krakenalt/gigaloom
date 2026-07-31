"""Canonical Operator Evidence Workspace projection contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

import pytest

from gigaloom.review.workspace.api import (
    MAX_CHANGED_FILES,
    ChangeSetProjection,
    EvidenceFreshness,
    EvidenceOmission,
    EvidenceOmissionReason,
    EvidenceReference,
    EvidenceSection,
    EvidenceWorkspaceProjection,
    EvidenceWorkspaceRun,
    NextActionReference,
    build_evidence_workspace,
)


RUN = EvidenceWorkspaceRun(
    run_id="run-1",
    session_id="session-1",
    owner_id="owner-1",
    workspace_id="workspace-1",
    status="succeeded",
    revision="run-rev-7",
)


def _reference(
    section: EvidenceSection,
    *,
    suffix: str = "1",
    freshness: EvidenceFreshness = EvidenceFreshness.CURRENT,
) -> EvidenceReference:
    return EvidenceReference(
        section=section,
        kind=f"{section.value}.snapshot",
        authority=f"review.{section.value}",
        resource_id=f"{section.value}-{suffix}",
        owner_id=RUN.owner_id,
        workspace_id=RUN.workspace_id,
        revision=f"{section.value}-rev-{suffix}",
        sha256=suffix * 64,
        state="ready",
        freshness=freshness,
    )


def _all_references() -> tuple[EvidenceReference, ...]:
    return tuple(
        _reference(section, suffix=str(index))
        for index, section in enumerate(
            (
                EvidenceSection.CANDIDATE,
                EvidenceSection.GATE,
                EvidenceSection.FINDINGS,
                EvidenceSection.CONTEXT,
                EvidenceSection.IMPACT,
                EvidenceSection.TRUST_FLOWS,
                EvidenceSection.COSTS,
                EvidenceSection.TERMINAL,
            ),
            start=1,
        )
    )


def _change_set(
    *,
    changed_files: tuple[str, ...] = ("src/gigaloom/a.py", "tests/test_a.py"),
    freshness: EvidenceFreshness = EvidenceFreshness.CURRENT,
) -> ChangeSetProjection:
    return ChangeSetProjection(
        authority="projects.environment",
        owner_id=RUN.owner_id,
        workspace_id=RUN.workspace_id,
        revision="change-rev-2",
        base_sha256="a" * 64,
        patch_sha256="b" * 64,
        changed_files=changed_files,
        freshness=freshness,
    )


def test_workspace_projection_is_canonical_digest_bound_and_content_free() -> None:
    references = list(_all_references())
    references[1] = _reference(
        EvidenceSection.GATE,
        suffix="2",
        freshness=EvidenceFreshness.STALE,
    )
    action = NextActionReference(
        action_id="approval-1",
        kind="approval.respond",
        authority="runtime.approvals",
        owner_id=RUN.owner_id,
        workspace_id=RUN.workspace_id,
        revision="approval-rev-3",
        sha256="c" * 64,
        consequence="external_write",
        expires_at="2026-07-31T00:00:00Z",
    )

    projection = build_evidence_workspace(
        run=RUN,
        references=reversed(references),
        change_set=_change_set(changed_files=("tests/test_a.py", "src/gigaloom/a.py")),
        next_actions=(action,),
    )
    payload: dict[str, Any] = projection.to_dict()

    assert payload["kind"] == "gigaloom.operator_evidence_workspace.v1"
    assert payload["staleness"] == {
        "has_stale_evidence": True,
        "stale_reference_count": 1,
        "sections": ["gate"],
    }
    assert payload["change_set"]["changed_files"] == [
        "src/gigaloom/a.py",
        "tests/test_a.py",
    ]
    assert [item["section"] for item in payload["references"]] == sorted(
        section.value
        for section in EvidenceSection
        if section is not EvidenceSection.CHANGE_SET
    )
    assert EvidenceWorkspaceProjection.from_dict(payload).to_dict() == payload
    assert (
        build_evidence_workspace(
            run=RUN,
            references=references,
            change_set=_change_set(),
            next_actions=(action,),
        ).projection_sha256
        == projection.projection_sha256
    )
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "prompt",
        "response_body",
        "terminal_output",
        "socket_path",
        "oauth_token",
    ):
        assert forbidden not in serialized


def test_partial_workspace_requires_explicit_omissions_for_every_missing_owner() -> (
    None
):
    reference = _reference(EvidenceSection.CONTEXT)
    omissions = tuple(
        EvidenceOmission(
            section=section,
            reason=(
                EvidenceOmissionReason.NOT_APPLICABLE
                if section in {EvidenceSection.CANDIDATE, EvidenceSection.GATE}
                else EvidenceOmissionReason.NOT_RECORDED
            ),
            authority=f"review.{section.value}",
        )
        for section in EvidenceSection
        if section is not EvidenceSection.CONTEXT
    )

    payload: dict[str, Any] = build_evidence_workspace(
        run=RUN,
        references=(reference,),
        omissions=reversed(omissions),
    ).to_dict()

    assert payload["change_set"] is None
    assert [item["section"] for item in payload["omissions"]] == sorted(
        section.value
        for section in EvidenceSection
        if section is not EvidenceSection.CONTEXT
    )
    assert payload["staleness"]["has_stale_evidence"] is False


def test_workspace_rejects_implicit_or_conflicting_availability() -> None:
    with pytest.raises(ValueError, match="require evidence or omission"):
        build_evidence_workspace(
            run=RUN,
            references=(_reference(EvidenceSection.CONTEXT),),
        )

    omissions = tuple(
        EvidenceOmission(
            section=section,
            reason=EvidenceOmissionReason.UNAVAILABLE,
            authority=f"review.{section.value}",
        )
        for section in EvidenceSection
    )
    with pytest.raises(ValueError, match="present and omitted"):
        build_evidence_workspace(
            run=RUN,
            references=(_reference(EvidenceSection.CONTEXT),),
            omissions=omissions,
        )


@pytest.mark.parametrize(
    ("owner_id", "workspace_id", "message"),
    [
        ("other-owner", RUN.workspace_id, "owner binding"),
        (RUN.owner_id, "other-workspace", "workspace binding"),
    ],
)
def test_workspace_rejects_cross_boundary_evidence(
    owner_id: str,
    workspace_id: str,
    message: str,
) -> None:
    references = list(_all_references())
    references[0] = replace(
        references[0],
        owner_id=owner_id,
        workspace_id=workspace_id,
    )

    with pytest.raises(ValueError, match=message):
        build_evidence_workspace(
            run=RUN,
            references=references,
            change_set=_change_set(),
        )


@pytest.mark.parametrize(
    "changed_files",
    [
        ("../secret.txt",),
        ("/tmp/private.txt",),
        ("dir\\ambiguous.txt",),
        tuple(f"file-{index}.txt" for index in range(MAX_CHANGED_FILES + 1)),
    ],
)
def test_workspace_rejects_unbounded_or_non_relative_changed_files(
    changed_files: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        build_evidence_workspace(
            run=RUN,
            references=_all_references(),
            change_set=_change_set(changed_files=changed_files),
        )


def test_workspace_parser_rejects_tampering_and_noncanonical_derived_state() -> None:
    payload = build_evidence_workspace(
        run=RUN,
        references=_all_references(),
        change_set=_change_set(),
    ).to_dict()

    tampered = deepcopy(payload)
    tampered["references"][0]["state"] = "failed"
    with pytest.raises(ValueError, match="digest does not match"):
        EvidenceWorkspaceProjection.from_dict(tampered)

    noncanonical = deepcopy(payload)
    noncanonical["staleness"]["has_stale_evidence"] = True
    with pytest.raises(ValueError, match="staleness is not canonical"):
        EvidenceWorkspaceProjection.from_dict(noncanonical)
