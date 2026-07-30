"""Public application facade for execution-owned source-to-sink admission."""

from gigaloom.execution.trust import SourceToSinkGuard, admit_sink_request

__all__ = ["SourceToSinkGuard", "admit_sink_request"]
