"""Immutable bounded screenshot artifact persistence."""

from __future__ import annotations

import base64
from pathlib import Path
import os
from typing import Protocol, runtime_checkable
from uuid import uuid4

from gigaloom.automation.evaluations.visual.browser import BrowserCaptureResult
from gigaloom.automation.evaluations.visual.redaction import VisualRedactionPolicy
from gigaloom.contracts import VisualArtifactReferenceV1
from gigaloom.contracts.operational_validation import validate_identity


REDACTED_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@runtime_checkable
class VisualArtifactStorePort(Protocol):
    """Public artifact boundary used by the Visual QA gate service."""

    def save_screenshots(
        self,
        *,
        gate_id: str,
        captures: tuple[BrowserCaptureResult, BrowserCaptureResult],
        redaction_policy: VisualRedactionPolicy,
    ) -> tuple[VisualArtifactReferenceV1, VisualArtifactReferenceV1]:
        """Persist exactly two safe screenshot artifacts."""


class FilesystemVisualArtifactStore:
    """Atomic immutable storage below one caller-owned artifact root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save_screenshots(
        self,
        *,
        gate_id: str,
        captures: tuple[BrowserCaptureResult, BrowserCaptureResult],
        redaction_policy: VisualRedactionPolicy,
    ) -> tuple[VisualArtifactReferenceV1, VisualArtifactReferenceV1]:
        """Persist safe PNGs or fixed placeholders when redaction failed."""
        validate_identity(gate_id, field_name="visual gate id")
        if len(captures) != 2 or len({item.viewport_id for item in captures}) != 2:
            raise ValueError("visual artifact store requires two distinct viewports")
        references = tuple(
            self._save_one(
                gate_id=gate_id,
                capture=capture,
                redaction_policy=redaction_policy,
            )
            for capture in sorted(captures, key=lambda item: item.viewport_id)
        )
        return references[0], references[1]

    def _save_one(
        self,
        *,
        gate_id: str,
        capture: BrowserCaptureResult,
        redaction_policy: VisualRedactionPolicy,
    ) -> VisualArtifactReferenceV1:
        safe = screenshot_is_redacted(capture, redaction_policy)
        payload = capture.screenshot_png if safe else REDACTED_PLACEHOLDER_PNG
        relative = Path("visual") / gate_id / f"{capture.viewport_id}.png"
        destination = (self._root / relative).resolve()
        if self._root not in destination.parents:
            raise ValueError("visual artifact path escapes its root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise ValueError("visual artifact is immutable")
        else:
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        return VisualArtifactReferenceV1(
            artifact_id=f"{gate_id}.{capture.viewport_id}",
            viewport_id=capture.viewport_id,
            relative_path=relative.as_posix(),
            artifact_digest=digest,
            media_type="image/png",
            byte_count=len(payload),
            redacted=True,
        )


def screenshot_is_redacted(
    capture: BrowserCaptureResult,
    policy: VisualRedactionPolicy,
) -> bool:
    """Return whether browser masking and secret scanning both passed."""
    return bool(
        capture.screenshot_secret_scan_passed
        and policy.required_ids.issubset(capture.masked_redaction_ids)
    )


__all__ = [
    "FilesystemVisualArtifactStore",
    "REDACTED_PLACEHOLDER_PNG",
    "VisualArtifactStorePort",
    "screenshot_is_redacted",
]
