"""Shared CLI output primitives."""

from __future__ import annotations

import json
from typing import Any


def print_json(value: Any) -> None:
    """Render one stable JSON document to stdout."""
    print(json.dumps(value, indent=2, sort_keys=True, default=str))
