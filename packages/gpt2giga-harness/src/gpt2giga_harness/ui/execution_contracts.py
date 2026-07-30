"""Route-local asynchronous execution contracts and central validation."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar, overload

from gpt2giga_harness.ui.mutation_contracts import (
    MUTATION_ROUTE_CONTRACTS,
    MutationClass,
)


class WorkloadClass(str, Enum):
    """Potentially blocking boundary owned by one route."""

    EVENT_LOOP_SAFE = "event_loop_safe_cpu"
    FILESYSTEM = "filesystem"
    SQLITE = "sqlite"
    NETWORK = "network"
    SUBPROCESS = "subprocess"
    DURABLE_JOB = "durable_job"
    STREAM = "stream"


class ExecutionAdapter(str, Enum):
    """Adapter that keeps a declared workload off the event-loop thread."""

    EVENT_LOOP = "event_loop"
    BOUNDED_THREAD = "bounded_thread"
    NATIVE_ASYNC = "native_async"
    DURABLE_WORKER = "durable_worker"
    ASYNC_STREAM = "async_stream"


class CancellationContract(str, Enum):
    """Meaning of request cancellation at the execution boundary."""

    REQUEST_SCOPED = "request_scoped"
    ATOMIC_COMPLETION = "atomic_completion"
    DURABLE_IDENTITY = "durable_identity"
    DISCONNECT_CLEANUP = "disconnect_cleanup"


class IdempotencyContract(str, Enum):
    """Replay behavior declared by a route."""

    READ_ONLY = "read_only"
    ATOMIC_STORE = "atomic_store"
    CLIENT_KEY = "client_key"
    DURABLE_JOB = "durable_job"
    STREAM_CURSOR = "stream_cursor"


@dataclass(frozen=True)
class RouteExecutionMetadata:
    """Execution choices declared next to one owning route handler."""

    workload: WorkloadClass
    adapter: ExecutionAdapter
    cancellation: CancellationContract
    idempotency: IdempotencyContract


@dataclass(frozen=True)
class RouteExecutionContract:
    """One exact route's semantic, workload, and bounded execution contract."""

    method: str
    path: str
    mutation_class: MutationClass
    workload: WorkloadClass
    storage_owner: str
    execution_owner: str
    adapter: ExecutionAdapter
    deadline_seconds: float | None
    cancellation: CancellationContract
    idempotency: IdempotencyContract
    max_response_bytes: int
    cursor: str | None
    latency_p95_ms: int

    @property
    def identity(self) -> tuple[str, str]:
        """Return the exact HTTP method and normalized FastAPI path."""
        return self.method, self.path


_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])
_METADATA_ATTRIBUTE = "__harness_route_execution_metadata__"


def route_execution(
    workload: WorkloadClass,
    adapter: ExecutionAdapter,
    cancellation: CancellationContract,
    idempotency: IdempotencyContract,
) -> Callable[[_Endpoint], _Endpoint]:
    """Attach execution metadata for the immediately owning route decorator."""
    metadata = RouteExecutionMetadata(
        workload=workload,
        adapter=adapter,
        cancellation=cancellation,
        idempotency=idempotency,
    )

    def declare(endpoint: _Endpoint) -> _Endpoint:
        setattr(endpoint, _METADATA_ATTRIBUTE, metadata)
        return endpoint

    return declare


@dataclass(frozen=True)
class _RouteExecutionPreset:
    """Compose FastAPI registration with one typed execution declaration."""

    metadata: RouteExecutionMetadata

    def __call__(
        self,
        registrar: Callable[..., Callable[[_Endpoint], _Endpoint]],
        *args: Any,
        **kwargs: Any,
    ) -> Callable[[_Endpoint], _Endpoint]:
        register = registrar(*args, **kwargs)

        def declare(endpoint: _Endpoint) -> _Endpoint:
            metadata = self.metadata
            declared = route_execution(
                metadata.workload,
                metadata.adapter,
                metadata.cancellation,
                metadata.idempotency,
            )(endpoint)
            return register(declared)

        return declare


def _preset(
    workload: WorkloadClass,
    adapter: ExecutionAdapter,
    cancellation: CancellationContract,
    idempotency: IdempotencyContract,
) -> _RouteExecutionPreset:
    return _RouteExecutionPreset(
        RouteExecutionMetadata(workload, adapter, cancellation, idempotency)
    )


class RouteExecutionDeclarations:
    """Readable route-local names for the supported execution combinations."""

    fs_read = _preset(
        WorkloadClass.FILESYSTEM,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    fs_atomic = _preset(
        WorkloadClass.FILESYSTEM,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    fs_async_atomic = _preset(
        WorkloadClass.FILESYSTEM,
        ExecutionAdapter.NATIVE_ASYNC,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    db_read = _preset(
        WorkloadClass.SQLITE,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    db_atomic = _preset(
        WorkloadClass.SQLITE,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    net_read = _preset(
        WorkloadClass.NETWORK,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    net_atomic = _preset(
        WorkloadClass.NETWORK,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    net_async_read = _preset(
        WorkloadClass.NETWORK,
        ExecutionAdapter.NATIVE_ASYNC,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    net_async_atomic = _preset(
        WorkloadClass.NETWORK,
        ExecutionAdapter.NATIVE_ASYNC,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    proc_read = _preset(
        WorkloadClass.SUBPROCESS,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    proc = _preset(
        WorkloadClass.SUBPROCESS,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    proc_async_read = _preset(
        WorkloadClass.SUBPROCESS,
        ExecutionAdapter.NATIVE_ASYNC,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    proc_async_atomic = _preset(
        WorkloadClass.SUBPROCESS,
        ExecutionAdapter.NATIVE_ASYNC,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )
    worker_job = _preset(
        WorkloadClass.DURABLE_JOB,
        ExecutionAdapter.DURABLE_WORKER,
        CancellationContract.DURABLE_IDENTITY,
        IdempotencyContract.DURABLE_JOB,
    )
    worker_client_key = _preset(
        WorkloadClass.DURABLE_JOB,
        ExecutionAdapter.DURABLE_WORKER,
        CancellationContract.DURABLE_IDENTITY,
        IdempotencyContract.CLIENT_KEY,
    )
    bounded_job = _preset(
        WorkloadClass.DURABLE_JOB,
        ExecutionAdapter.BOUNDED_THREAD,
        CancellationContract.DURABLE_IDENTITY,
        IdempotencyContract.DURABLE_JOB,
    )
    stream = _preset(
        WorkloadClass.STREAM,
        ExecutionAdapter.ASYNC_STREAM,
        CancellationContract.DISCONNECT_CLEANUP,
        IdempotencyContract.STREAM_CURSOR,
    )
    loop_read = _preset(
        WorkloadClass.EVENT_LOOP_SAFE,
        ExecutionAdapter.EVENT_LOOP,
        CancellationContract.REQUEST_SCOPED,
        IdempotencyContract.READ_ONLY,
    )
    loop_atomic = _preset(
        WorkloadClass.EVENT_LOOP_SAFE,
        ExecutionAdapter.EVENT_LOOP,
        CancellationContract.ATOMIC_COMPLETION,
        IdempotencyContract.ATOMIC_STORE,
    )


routes = RouteExecutionDeclarations()


_FRAMEWORK_ROUTE_METADATA = {
    ("GET", "/openapi.json"): RouteExecutionMetadata(
        workload=WorkloadClass.EVENT_LOOP_SAFE,
        adapter=ExecutionAdapter.EVENT_LOOP,
        cancellation=CancellationContract.REQUEST_SCOPED,
        idempotency=IdempotencyContract.READ_ONLY,
    ),
    ("HEAD", "/openapi.json"): RouteExecutionMetadata(
        workload=WorkloadClass.EVENT_LOOP_SAFE,
        adapter=ExecutionAdapter.EVENT_LOOP,
        cancellation=CancellationContract.REQUEST_SCOPED,
        idempotency=IdempotencyContract.READ_ONLY,
    ),
}

_OPAQUE_CURSOR_IDENTITIES = frozenset(
    {
        ("GET", "/api/runs"),
        ("GET", "/api/runs/{run_id}/trace"),
        ("GET", "/api/cockpit/sessions"),
        ("GET", "/api/cockpit/sessions/{session_id}/artifacts"),
        ("GET", "/api/cockpit/sessions/{session_id}/events"),
        ("GET", "/api/cockpit/sessions/{session_id}/messages"),
        ("GET", "/api/cockpit/sessions/{session_id}/runs"),
        ("GET", "/api/workbench/state"),
    }
)
_MUTATION_BY_IDENTITY = {
    contract.identity: contract for contract in MUTATION_ROUTE_CONTRACTS
}


def _storage_owner(workload: WorkloadClass) -> str:
    return {
        WorkloadClass.EVENT_LOOP_SAFE: "none",
        WorkloadClass.FILESYSTEM: "filesystem_store",
        WorkloadClass.SQLITE: "runtime_sqlite",
        WorkloadClass.NETWORK: "proxy_client",
        WorkloadClass.SUBPROCESS: "process_boundary",
        WorkloadClass.DURABLE_JOB: "runtime_job_store",
        WorkloadClass.STREAM: "durable_event_store",
    }[workload]


def _execution_owner(adapter: ExecutionAdapter, workload: WorkloadClass) -> str:
    if adapter is ExecutionAdapter.EVENT_LOOP:
        return "event_loop_cpu"
    if adapter is ExecutionAdapter.DURABLE_WORKER:
        return "durable_job_dispatcher"
    if adapter is ExecutionAdapter.ASYNC_STREAM:
        return "bounded_sse_stream"
    return f"bounded_{workload.value}_offload"


def route_execution_metadata(
    endpoint: Callable[..., Any],
) -> RouteExecutionMetadata | None:
    """Return metadata attached next to an owning route handler."""
    metadata = getattr(endpoint, _METADATA_ATTRIBUTE, None)
    return metadata if isinstance(metadata, RouteExecutionMetadata) else None


def build_route_execution_contract(
    method: str,
    path: str,
    endpoint: Callable[..., Any],
) -> RouteExecutionContract | None:
    """Combine route-local execution metadata with central bounded invariants."""
    identity = (method.upper(), path)
    metadata = route_execution_metadata(endpoint) or _FRAMEWORK_ROUTE_METADATA.get(
        identity
    )
    if metadata is None:
        return None
    mutation = _MUTATION_BY_IDENTITY.get(identity)
    workload = metadata.workload
    return RouteExecutionContract(
        method=identity[0],
        path=identity[1],
        mutation_class=(
            mutation.mutation_class if mutation is not None else MutationClass.READ_ONLY
        ),
        workload=workload,
        storage_owner=_storage_owner(workload),
        execution_owner=_execution_owner(metadata.adapter, workload),
        adapter=metadata.adapter,
        deadline_seconds=(None if workload is WorkloadClass.STREAM else 10.0),
        cancellation=metadata.cancellation,
        idempotency=metadata.idempotency,
        max_response_bytes=(
            16 * 1024 * 1024
            if "/files/" in path
            or path.endswith(("/diff", "/patch", "/support-bundle"))
            else 1024 * 1024
        ),
        cursor=(
            "Last-Event-ID"
            if workload is WorkloadClass.STREAM
            else "opaque"
            if identity in _OPAQUE_CURSOR_IDENTITIES
            else None
        ),
        latency_p95_ms=(1500 if not path.startswith("/api/") else 500),
    )


class _RouteExecutionInventory(Sequence[RouteExecutionContract]):
    """Stable public view over the latest centrally validated app inventory."""

    def __init__(self) -> None:
        self._contracts: tuple[RouteExecutionContract, ...] = ()

    @overload
    def __getitem__(self, index: int) -> RouteExecutionContract: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[RouteExecutionContract]: ...

    def __getitem__(
        self, index: int | slice
    ) -> RouteExecutionContract | Sequence[RouteExecutionContract]:
        return self._contracts[index]

    def __len__(self) -> int:
        return len(self._contracts)

    def __iter__(self) -> Iterator[RouteExecutionContract]:
        return iter(self._contracts)

    def replace(self, contracts: Sequence[RouteExecutionContract]) -> None:
        self._contracts = tuple(sorted(contracts, key=lambda item: item.identity))


ROUTE_EXECUTION_CONTRACTS = _RouteExecutionInventory()


def route_identities(routes: Sequence[object]) -> frozenset[tuple[str, str]]:
    """Recursively expand included FastAPI routers into route identities."""
    identities: set[tuple[str, str]] = set()
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            identities.update(route_identities(included.routes))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or ()
        if isinstance(path, str):
            identities.update((method, path) for method in methods)
    return frozenset(identities)


def _contracts_from_routes(
    routes: Sequence[object],
) -> tuple[RouteExecutionContract, ...]:
    contracts: list[RouteExecutionContract] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            contracts.extend(_contracts_from_routes(included.routes))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or ()
        endpoint = getattr(route, "endpoint", None)
        if not isinstance(path, str) or not callable(endpoint):
            continue
        bound = getattr(route, "execution_contract", None)
        for method in methods:
            contract = (
                bound
                if isinstance(bound, RouteExecutionContract)
                and bound.identity == (method, path)
                else build_route_execution_contract(method, path, endpoint)
            )
            if contract is not None:
                contracts.append(contract)
    return tuple(contracts)


def execution_contract_errors(routes: Sequence[object]) -> tuple[str, ...]:
    """Return deterministic route-drift and declaration errors."""
    errors: list[str] = []
    contracts = _contracts_from_routes(routes)
    declared_list = [contract.identity for contract in contracts]
    duplicates = sorted(
        identity for identity in set(declared_list) if declared_list.count(identity) > 1
    )
    if duplicates:
        errors.append(f"duplicate execution contracts: {duplicates}")
    runtime = route_identities(routes)
    declared = frozenset(declared_list)
    if missing := sorted(runtime - declared):
        errors.append(f"unclassified routes: {missing}")
    if stale := sorted(declared - runtime):
        errors.append(f"contracts without routes: {stale}")
    for contract in contracts:
        label = f"{contract.method} {contract.path}"
        if not contract.storage_owner or not contract.execution_owner:
            errors.append(f"{label} lacks storage or execution owner")
        if (
            contract.deadline_seconds is None
            and contract.workload is not WorkloadClass.STREAM
        ):
            errors.append(f"{label} lacks a deadline")
        if contract.max_response_bytes <= 0 or contract.latency_p95_ms <= 0:
            errors.append(f"{label} has invalid payload or latency budget")
        if contract.workload is WorkloadClass.STREAM and not contract.cursor:
            errors.append(f"{label} stream lacks a cursor contract")
    return tuple(errors)


def install_execution_contracts(app: object) -> None:
    """Fail closed on route drift and expose the validated inventory."""
    routes = getattr(app, "routes")
    errors = execution_contract_errors(routes)
    if errors:
        raise RuntimeError("Harness execution contract invalid: " + "; ".join(errors))
    contracts = _contracts_from_routes(routes)
    ROUTE_EXECUTION_CONTRACTS.replace(contracts)
    state = getattr(app, "state")
    state.harness_execution_contracts = tuple(ROUTE_EXECUTION_CONTRACTS)
