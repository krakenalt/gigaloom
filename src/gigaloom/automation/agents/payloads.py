"""Payloads for the agents subcontext."""

from __future__ import annotations

from typing import Any, Mapping
from gigaloom.types import redact_secrets
from .models import (
    AgentExecutionPlan as AgentExecutionPlan,
    AgentOptionStatus as AgentOptionStatus,
    AgentProfile as AgentProfile,
)
from .planning import build_agent_execution_plan as build_agent_execution_plan
from .profiles import agent_profile_to_dict as agent_profile_to_dict


def agent_execution_plan_to_dict(plan: AgentExecutionPlan) -> dict[str, Any]:
    """Serialize one immutable option plan without exposing probe commands."""
    payload = {
        "schema_version": plan.schema_version,
        "harness_id": plan.harness_id,
        "invocation_mode": plan.invocation_mode,
        "queueable": plan.queueable,
        "binary_version": plan.binary_version,
        "capability_evidence": plan.capability_evidence,
        "options": {
            name: {
                "status": resolution.status.value,
                "requested": resolution.requested,
                "effective": resolution.effective,
                "enforcement_source": resolution.enforcement_source,
                "detail": resolution.detail,
            }
            for name, resolution in plan.options.items()
        },
        "adapter_options": dict(plan.adapter_options),
        "errors": list(plan.errors),
        "warnings": list(plan.warnings),
    }
    redacted = dict(redact_secrets(payload))
    options = redacted.get("options")
    token_resolution = plan.options.get("budgets.max_tokens")
    if isinstance(options, Mapping) and token_resolution is not None:
        redacted["options"] = {
            **dict(options),
            "budgets.max_tokens": {
                "status": token_resolution.status.value,
                "requested": token_resolution.requested,
                "effective": token_resolution.effective,
                "enforcement_source": token_resolution.enforcement_source,
                "detail": token_resolution.detail,
            },
        }
    return redacted


def agent_run_payload(
    profile: AgentProfile,
    prompt: str,
    *,
    workspace: str,
    harness: Any,
    default_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a durable manual-run payload with an immutable redacted snapshot."""
    execution_plan = build_agent_execution_plan(
        profile,
        harness,
        default_timeout_seconds=default_timeout_seconds,
    )
    if not execution_plan.queueable:
        raise ValueError(
            "Agent profile options are not executable: "
            + "; ".join(execution_plan.errors)
        )
    execution_plan_payload = agent_execution_plan_to_dict(execution_plan)
    effective_prompt = (
        f"Agent role instructions:\n{profile.instructions}\n\nTask:\n{prompt.strip()}"
    )
    payload = {
        "prompt": effective_prompt,
        "harness_id": profile.harness_id,
        "model": profile.model,
        "api_mode": profile.api_mode,
        "invocation_mode": profile.invocation_mode,
        "mode": profile.mode,
        "workspace_policy": profile.workspace_policy,
        "workspace": workspace,
        "permission_profile": profile.permission_profile,
        "agent_id": profile.id,
        "agent_profile_snapshot": agent_profile_to_dict(profile),
        "agent_execution_plan": execution_plan_payload,
        "timeout_seconds": profile.budgets.timeout_seconds,
        "max_attempts": profile.budgets.max_attempts,
        "extra": {
            "agent_id": profile.id,
            "agent_execution_plan": execution_plan_payload,
            "agent_adapter_options": dict(execution_plan.adapter_options),
            "tool_ids": list(profile.tool_ids),
            "tool_bindings": [
                {
                    "server_id": server_id,
                    "enforcement": "managed_headless_snapshot",
                    "observability": "opaque_unless_structured_adapter",
                }
                for server_id in profile.tool_ids
            ],
            "skills": list(profile.skills),
            "prompt_files": list(profile.prompt_files),
            "memory_selectors": list(profile.memory_selectors),
            "context_selectors": list(profile.context_selectors),
            "max_tokens": profile.budgets.max_tokens,
            "reasoning_effort": profile.reasoning_effort,
            "max_concurrency": profile.budgets.max_concurrency,
            "expected_artifact": profile.expected_artifact,
        },
    }
    execution_transport = execution_plan.adapter_options.get("execution_transport")
    if execution_transport is not None:
        payload["execution_transport"] = execution_transport
    return payload


def apply_agent_run_overrides(
    payload: Mapping[str, Any],
    *,
    workspace_policy: str,
    permission_profile: str,
    timeout_seconds: int | float | None,
    max_attempts: int,
) -> dict[str, Any]:
    """Keep a persisted AgentProfile plan aligned with coordinator overrides."""
    prepared = dict(payload)
    prepared.update(
        {
            "workspace_policy": workspace_policy,
            "permission_profile": permission_profile,
            "timeout_seconds": timeout_seconds,
            "max_attempts": max_attempts,
        }
    )
    source_plan = payload.get("agent_execution_plan")
    if not isinstance(source_plan, Mapping):
        return prepared
    plan = dict(source_plan)
    source_options = plan.get("options")
    options = (
        {
            name: dict(value)
            for name, value in source_options.items()
            if isinstance(value, Mapping)
        }
        if isinstance(source_options, Mapping)
        else {}
    )
    for name, effective in (
        ("workspace_policy", workspace_policy),
        ("permission_profile", permission_profile),
        ("budgets.timeout_seconds", timeout_seconds),
        ("budgets.max_attempts", max_attempts),
    ):
        resolution = options.get(name)
        if resolution is None:
            continue
        resolution.update(
            {
                "status": AgentOptionStatus.EFFECTIVE.value,
                "effective": effective,
                "enforcement_source": "workflow_or_schedule_coordinator",
                "detail": (
                    "The coordinator overrides this value for the concrete child job "
                    "while preserving the profile request."
                ),
            }
        )
    plan["options"] = options
    prepared["agent_execution_plan"] = plan
    extra = dict(payload.get("extra") or {})
    extra["agent_execution_plan"] = plan
    prepared["extra"] = extra
    return prepared
