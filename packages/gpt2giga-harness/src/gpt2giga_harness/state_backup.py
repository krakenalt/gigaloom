"""Compatibility alias for project-state backups."""

import sys

from gpt2giga_harness.projects import backup as _implementation

sys.modules[__name__] = _implementation
