"""Typed Route Advisor failures."""


class RouteAdvisorError(ValueError):
    """Base error for deterministic route decisions."""


class RouteRankingError(RouteAdvisorError):
    """Raised when an eligible route set cannot be ranked safely."""


class RouteOverrideError(RouteAdvisorError):
    """Raised when a manual override is not valid for a decision."""


__all__ = ["RouteAdvisorError", "RouteOverrideError", "RouteRankingError"]
