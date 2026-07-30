"""Authoritative JSONL readers used to rebuild record query projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping


def read_jsonl_with_offsets(
    path: Path,
    parser: Callable[[Mapping[str, Any]], Any],
) -> list[tuple[Any, int]]:
    """Return parsed rows paired with their durable starting byte offsets."""
    if not path.exists():
        return []
    rows: list[tuple[Any, int]] = []
    with path.open("rb") as handle:
        while line := handle.readline():
            position = handle.tell() - len(line)
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, Mapping):
                rows.append((parser(payload), position))
    return rows
