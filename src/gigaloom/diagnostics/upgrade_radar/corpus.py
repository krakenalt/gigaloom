"""Bounded loader for content-free sealed upgrade corpora."""

from __future__ import annotations

import json
from pathlib import Path

from gigaloom.diagnostics.upgrade_radar.contracts import (
    SealedCorpusV1,
    sealed_corpus_from_dict,
)


MAX_SEALED_CORPUS_BYTES = 64 * 1024


def load_sealed_corpus(path: str | Path) -> SealedCorpusV1:
    """Load one regular JSON fixture without following symlinks or using network."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("sealed corpus path must be a regular file")
    payload = target.read_bytes()
    if not payload or len(payload) > MAX_SEALED_CORPUS_BYTES:
        raise ValueError("sealed corpus file exceeds the bounded size policy")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sealed corpus is not valid JSON") from exc
    return sealed_corpus_from_dict(decoded)


__all__ = ["MAX_SEALED_CORPUS_BYTES", "load_sealed_corpus"]
