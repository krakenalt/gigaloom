"""Compatibility alias for the project bounded context."""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gigaloom.projects.api import project_id_for_root as project_id_for_root
else:
    from gigaloom.projects import api as _implementation

    sys.modules[__name__] = _implementation
