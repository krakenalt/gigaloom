"""Deterministic non-interactive execution contracts and runner."""

from gigaloom.execution.headless.admission import (
    MAX_HEADLESS_PROMPT_BYTES,
    HeadlessAdmissionError,
    HeadlessPathAuthority,
    HeadlessRunInput,
)
from gigaloom.execution.headless.cancellation import (
    HeadlessCancellationScope,
    HeadlessCancellationToken,
)
from gigaloom.execution.headless.contracts import (
    HeadlessBackendStatus,
    HeadlessExecutionPort,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessProgressSinkPort,
    HeadlessRouteResolverPort,
    HeadlessRouteSelectionV1,
    HeadlessRunResult,
    PreparedHeadlessRun,
)
from gigaloom.execution.headless.runner import HeadlessRunner
from gigaloom.execution.headless.events import (
    CanonicalJsonlEventWriter,
    HeadlessEventStreamError,
    RunnerOwnedProgressSink,
    emit_unadmitted_terminal,
)
from gigaloom.execution.headless.results import (
    HEADLESS_PARTIAL_RECEIPT_REF,
    HEADLESS_RESULT_REF,
    HEADLESS_TERMINAL_RECEIPT_REF,
    HeadlessResultStore,
    HeadlessResultStoreError,
)
from gigaloom.execution.headless.runner import write_headless_diagnostic

__all__ = [
    "MAX_HEADLESS_PROMPT_BYTES",
    "CanonicalJsonlEventWriter",
    "HEADLESS_PARTIAL_RECEIPT_REF",
    "HEADLESS_RESULT_REF",
    "HEADLESS_TERMINAL_RECEIPT_REF",
    "HeadlessAdmissionError",
    "HeadlessBackendStatus",
    "HeadlessCancellationScope",
    "HeadlessCancellationToken",
    "HeadlessExecutionPort",
    "HeadlessExecutionRequest",
    "HeadlessExecutionResult",
    "HeadlessProgressSinkPort",
    "HeadlessPathAuthority",
    "HeadlessRouteResolverPort",
    "HeadlessRouteSelectionV1",
    "HeadlessRunInput",
    "HeadlessRunResult",
    "HeadlessRunner",
    "HeadlessEventStreamError",
    "HeadlessResultStore",
    "HeadlessResultStoreError",
    "PreparedHeadlessRun",
    "RunnerOwnedProgressSink",
    "emit_unadmitted_terminal",
    "write_headless_diagnostic",
]
