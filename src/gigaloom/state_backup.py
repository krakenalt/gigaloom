"""Compatibility alias for project-state backups."""

import sys

from gigaloom.projects import backup as _implementation

sys.modules[__name__] = _implementation
