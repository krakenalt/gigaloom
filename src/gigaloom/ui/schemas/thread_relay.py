"""Bounded HTTP request schemas for route-local Thread Relay actions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ThreadRelaySendRequest(BaseModel):
    """One user-role delivery request bound to exact target state."""

    source: Literal["gigaloom", "codex", "acp"] = "gigaloom"
    thread_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=16_384)
    intent: Literal["message", "follow_up", "steer"] = "message"
    author_mode: Literal["user_authored", "agent_proposed_user_approved"] = (
        "user_authored"
    )
    expected_target_revision: str = Field(min_length=1, max_length=256)
    expected_active_turn_id: str | None = Field(default=None, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    expires_at: datetime
    attachment_refs: list[str] = Field(default_factory=list, max_length=16)


__all__ = ["ThreadRelaySendRequest"]
