"""Aggregate bounded operator-surface mutation contract families."""

from gigaloom.ui import agent_runtime_mutation_contracts as agent_runtime
from gigaloom.ui import credential_mutation_contracts as credential_operator


CONFORMANCE_EVIDENCE = (
    *agent_runtime.CONFORMANCE_EVIDENCE,
    *credential_operator.CONFORMANCE_EVIDENCE,
)
MUTATION_ROUTE_CONTRACTS = (
    *agent_runtime.MUTATION_ROUTE_CONTRACTS,
    *credential_operator.MUTATION_ROUTE_CONTRACTS,
)


__all__ = ["CONFORMANCE_EVIDENCE", "MUTATION_ROUTE_CONTRACTS"]
