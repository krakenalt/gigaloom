"""Screenshot redaction policy and adapter attestations."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.contracts.operational_validation import (
    canonical_digest,
    validate_identity,
    validate_text,
)


MAX_REDACTION_SELECTORS = 64


@dataclass(frozen=True, slots=True)
class ScreenshotRedactionSpec:
    """One browser-side selector mask, never retained in gate evidence."""

    redaction_id: str
    selector: str
    required: bool = True

    def __post_init__(self) -> None:
        validate_identity(self.redaction_id, field_name="visual redaction id")
        validate_text(
            self.selector,
            field_name="visual redaction selector",
            max_chars=512,
        )
        if not isinstance(self.required, bool):
            raise ValueError("visual redaction required state is invalid")

    @property
    def digest(self) -> str:
        """Return a content-free binding for one selector mask."""
        return canonical_digest(
            {
                "redaction_id": self.redaction_id,
                "required": self.required,
                "selector_digest": canonical_digest(self.selector),
            }
        )


@dataclass(frozen=True, slots=True)
class VisualRedactionPolicy:
    """Bounded browser masking plus mandatory screenshot secret scanning."""

    selectors: tuple[ScreenshotRedactionSpec, ...] = ()
    require_secret_scan: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.selectors, tuple)
            or len(self.selectors) > MAX_REDACTION_SELECTORS
            or any(
                not isinstance(item, ScreenshotRedactionSpec) for item in self.selectors
            )
        ):
            raise ValueError("visual redaction selectors must be a bounded tuple")
        ids = [item.redaction_id for item in self.selectors]
        if len(ids) != len(set(ids)):
            raise ValueError("visual redaction ids must be unique")
        if self.require_secret_scan is not True:
            raise ValueError("visual screenshot secret scan cannot be disabled")

    @property
    def digest(self) -> str:
        """Return the stable redaction-policy digest."""
        return canonical_digest(
            {
                "require_secret_scan": self.require_secret_scan,
                "selectors": [item.digest for item in self.selectors],
            }
        )

    @property
    def required_ids(self) -> frozenset[str]:
        """Return required mask identities without exposing selectors."""
        return frozenset(item.redaction_id for item in self.selectors if item.required)


__all__ = ["ScreenshotRedactionSpec", "VisualRedactionPolicy"]
