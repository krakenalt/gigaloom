"""Exact, non-invented ACP token, context, and cost projections."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any

from acp.schema import Cost, Usage, UsageUpdate


@dataclass(frozen=True, slots=True)
class AcpCostV1:
    """Provider-reported cost without local estimation."""

    amount: str | None
    currency: str | None
    quality: str


@dataclass(frozen=True, slots=True)
class AcpTokenUsageV1:
    """Exact provider token counters."""

    total_tokens: int
    input_tokens: int
    output_tokens: int
    thought_tokens: int | None = None
    cached_read_tokens: int | None = None
    cached_write_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class AcpContextUsageV1:
    """Exact context-window occupancy and optional provider cost."""

    used: int
    size: int
    cost: AcpCostV1


def project_cost(value: Cost | None) -> AcpCostV1:
    """Project only a finite, non-negative provider-reported cost."""
    if value is None:
        return AcpCostV1(None, None, "unknown")
    if not math.isfinite(value.amount) or value.amount < 0:
        raise ValueError("ACP cost must be finite and non-negative")
    try:
        amount = format(Decimal(str(value.amount)), "f")
    except InvalidOperation as exc:
        raise ValueError("ACP cost is invalid") from exc
    return AcpCostV1(amount, value.currency, "exact")


def project_token_usage(value: Usage | None) -> AcpTokenUsageV1 | None:
    """Project exact prompt response counters when the agent supplied them."""
    if value is None:
        return None
    payload = value.model_dump(mode="python", by_alias=False)
    counters = {key: item for key, item in payload.items() if item is not None}
    if any(isinstance(item, bool) or item < 0 for item in counters.values()):
        raise ValueError("ACP usage counters must be non-negative integers")
    return AcpTokenUsageV1(**counters)


def project_context_usage(value: UsageUpdate) -> AcpContextUsageV1:
    """Project one exact context update."""
    if value.used < 0 or value.size < 1 or value.used > value.size:
        raise ValueError("ACP context usage is outside its declared window")
    return AcpContextUsageV1(value.used, value.size, project_cost(value.cost))


def usage_payload(value: AcpContextUsageV1 | AcpTokenUsageV1) -> dict[str, Any]:
    """Return the provider-neutral JSON projection for an ephemeral event."""
    if isinstance(value, AcpContextUsageV1):
        return {
            "used": value.used,
            "size": value.size,
            "cost": {
                "amount": value.cost.amount,
                "currency": value.cost.currency,
                "quality": value.cost.quality,
            },
        }
    return {
        key: item
        for key, item in {
            "total_tokens": value.total_tokens,
            "input_tokens": value.input_tokens,
            "output_tokens": value.output_tokens,
            "thought_tokens": value.thought_tokens,
            "cached_read_tokens": value.cached_read_tokens,
            "cached_write_tokens": value.cached_write_tokens,
        }.items()
        if item is not None
    }
