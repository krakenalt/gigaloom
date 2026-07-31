"""Compatibility alias for :mod:`gigaloom.skills.catalog_proxy.client`."""

import sys

from .skills.catalog_proxy import client as _implementation

sys.modules[__name__] = _implementation
