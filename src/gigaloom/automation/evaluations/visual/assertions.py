"""Bounded content-free DOM assertion evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gigaloom.contracts import OperationalEvidenceStatus, OperationalEvidenceV1
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    validate_digest,
    validate_identity,
    validate_text,
)


class DomAssertionKind(str, Enum):
    """Supported deterministic DOM assertions."""

    EXISTS = "exists"
    VISIBLE = "visible"
    TEXT_DIGEST = "text_digest"


@dataclass(frozen=True, slots=True)
class DomAssertionSpec:
    """One bounded assertion whose retained evidence contains no page text."""

    assertion_id: str
    selector: str
    kind: DomAssertionKind
    expected_text_digest: str | None = None

    def __post_init__(self) -> None:
        validate_identity(self.assertion_id, field_name="visual assertion id")
        validate_text(
            self.selector,
            field_name="visual assertion selector",
            max_chars=512,
        )
        if not isinstance(self.kind, DomAssertionKind):
            raise ValueError("visual DOM assertion kind is invalid")
        if self.kind is DomAssertionKind.TEXT_DIGEST:
            validate_digest(
                self.expected_text_digest,
                field_name="visual expected text digest",
            )
        elif self.expected_text_digest is not None:
            raise ValueError("visual text digest is allowed only for text assertions")

    @property
    def digest(self) -> str:
        """Return the stable content-free assertion policy digest."""
        return canonical_digest(
            {
                "assertion_id": self.assertion_id,
                "expected_text_digest": self.expected_text_digest,
                "kind": self.kind.value,
                "selector_digest": canonical_digest(self.selector),
            }
        )


def evaluate_dom_assertion(
    spec: DomAssertionSpec,
    *,
    viewport_id: str,
    matched_count: int,
    visible_count: int,
    text_digest: str | None,
) -> OperationalEvidenceV1:
    """Evaluate one browser DOM observation without retaining selector or text."""
    if spec.kind is DomAssertionKind.EXISTS:
        passed = matched_count > 0
    elif spec.kind is DomAssertionKind.VISIBLE:
        passed = visible_count > 0
    else:
        passed = matched_count > 0 and text_digest == spec.expected_text_digest
    result_digest = canonical_digest(
        {
            "assertion_digest": spec.digest,
            "matched_count": matched_count,
            "passed": passed,
            "text_digest": text_digest,
            "viewport_id": viewport_id,
            "visible_count": visible_count,
        }
    )
    return OperationalEvidenceV1(
        evidence_id=f"{spec.assertion_id}.{viewport_id}",
        kind=f"visual.dom.{spec.kind.value}",
        status=(
            OperationalEvidenceStatus.PASSED
            if passed
            else OperationalEvidenceStatus.FAILED
        ),
        evidence_digest=result_digest,
        reason_code="assertion_passed" if passed else "assertion_failed",
        source_digest=spec.digest,
    )


__all__ = ["DomAssertionKind", "DomAssertionSpec", "evaluate_dom_assertion"]
