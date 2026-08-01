"""Bounded loader for content-free sealed upgrade corpora."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from gigaloom.diagnostics.upgrade_radar.contracts import (
    SealedCorpusV1,
    sealed_corpus_from_dict,
)


MAX_SEALED_CORPUS_BYTES = 64 * 1024
_NAMED_CORPORA = {
    "sealed-smoke": ("evidence", "upgrade_radar", "v1", "sealed-smoke.json"),
}


def load_sealed_corpus(path: str | Path) -> SealedCorpusV1:
    """Load one regular JSON fixture without following symlinks or using network."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("sealed corpus path must be a regular file")
    return _decode_sealed_corpus(target.read_bytes())


def load_named_sealed_corpus(corpus_id: str) -> SealedCorpusV1:
    """Load one packaged content-free corpus by its exact public identity."""
    relative = _NAMED_CORPORA.get(corpus_id)
    if relative is None:
        raise ValueError("unknown sealed corpus")
    payload = resources.files("gigaloom").joinpath(*relative).read_bytes()
    corpus = _decode_sealed_corpus(payload)
    if corpus.corpus_id != corpus_id:
        raise ValueError("sealed corpus identity does not match its package name")
    return corpus


def _decode_sealed_corpus(payload: bytes) -> SealedCorpusV1:
    if not payload or len(payload) > MAX_SEALED_CORPUS_BYTES:
        raise ValueError("sealed corpus file exceeds the bounded size policy")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sealed corpus is not valid JSON") from exc
    return sealed_corpus_from_dict(decoded)


__all__ = [
    "MAX_SEALED_CORPUS_BYTES",
    "load_named_sealed_corpus",
    "load_sealed_corpus",
]
