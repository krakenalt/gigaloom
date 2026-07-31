"""Candidate snapshot construction checks."""

from gigaloom.execution.route_advisor.models import StructuredRouteCandidateV1


def canonicalize_candidates(
    candidates: tuple[StructuredRouteCandidateV1, ...],
) -> tuple[StructuredRouteCandidateV1, ...]:
    """Return candidates in stable route-id order and reject duplicates."""
    if not isinstance(candidates, tuple):
        raise ValueError("route candidates must be a tuple")
    if not all(isinstance(item, StructuredRouteCandidateV1) for item in candidates):
        raise ValueError("route candidates are invalid")
    route_ids = tuple(item.route_id for item in candidates)
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("route candidate ids must be unique")
    return tuple(sorted(candidates, key=lambda item: item.route_id))


__all__ = ["canonicalize_candidates"]
