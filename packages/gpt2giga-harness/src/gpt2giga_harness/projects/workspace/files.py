"""Content-free metadata for admitted workspace files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gpt2giga_harness.attachments import limits as attachment_limits
from gpt2giga_harness.attachments import mime as attachment_mime

AttachmentLimits = attachment_limits.AttachmentLimits
detect_attachment_kind = attachment_mime.detect_attachment_kind
detect_mime_type = attachment_mime.detect_mime_type
normalize_workspace_file = attachment_limits.normalize_workspace_file
validate_size = attachment_limits.validate_size


def workspace_file_metadata(
    workspace_root: str | Path,
    path: str | Path,
    *,
    limits: AttachmentLimits = AttachmentLimits(),
) -> dict[str, Any]:
    """Return safe metadata for one workspace file without exposing contents."""
    resolved, relative = normalize_workspace_file(workspace_root, path, limits)
    size = resolved.stat().st_size
    validate_size(size, current_total_bytes=0, limits=limits)
    sample = _read_sample(resolved)
    mime_type = detect_mime_type(relative, None, sample)
    kind = detect_attachment_kind(relative, mime_type, sample)
    return {
        "path": relative,
        "name": Path(relative).name,
        "mime_type": mime_type,
        "kind": kind.value,
        "size_bytes": size,
    }


def _read_sample(path: Path, limit: int = 8192) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)
