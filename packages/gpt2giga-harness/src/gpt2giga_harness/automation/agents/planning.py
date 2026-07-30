"""Planning for the agents subcontext."""

from __future__ import annotations

from typing import Any
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.automation.ports import (
    DurableStructuredAdmissionError,
    admitted_durable_structured_capabilities,
)
from .models import (
    AgentExecutionPlan as AgentExecutionPlan,
    AgentOptionResolution as AgentOptionResolution,
    AgentOptionStatus as AgentOptionStatus,
    AgentProfile as AgentProfile,
)


def build_agent_execution_plan(
    profile: AgentProfile,
    harness: Any,
    *,
    default_timeout_seconds: float | None = None,
) -> AgentExecutionPlan:
    """Resolve requested profile fields against one adapter capability snapshot."""
    spec = harness.spec()
    if spec.id != profile.harness_id:
        raise ValueError(
            f"Agent profile harness {profile.harness_id} does not match {spec.id}"
        )
    options: dict[str, AgentOptionResolution] = {}
    adapter_options: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []

    def add(
        name: str,
        *,
        status: AgentOptionStatus,
        requested: Any,
        effective: Any,
        source: str,
        detail: str,
    ) -> None:
        options[name] = AgentOptionResolution(
            status=status,
            requested=requested,
            effective=effective,
            enforcement_source=source,
            detail=detail,
        )

    add(
        "instructions",
        status=AgentOptionStatus.EFFECTIVE,
        requested=profile.instructions,
        effective="prepended_to_user_prompt",
        source="harness_prompt",
        detail="Harness prepends the immutable role instructions to the submitted task.",
    )
    add(
        "model",
        status=AgentOptionStatus.EFFECTIVE,
        requested=profile.model,
        effective=profile.model or "runtime_default",
        source="adapter_cli",
        detail="The adapter pins the selected model through fixed argv or environment.",
    )
    add(
        "api_mode",
        status=AgentOptionStatus.EFFECTIVE,
        requested=profile.api_mode,
        effective=profile.api_mode,
        source="harness_proxy",
        detail="Harness selects and preflights the exact compatibility route.",
    )
    if profile.invocation_mode == "headless":
        add(
            "invocation_mode",
            status=AgentOptionStatus.EFFECTIVE,
            requested=profile.invocation_mode,
            effective="headless",
            source="durable_job",
            detail="Agent runs are submitted through the durable headless worker.",
        )
    else:
        try:
            capabilities = admitted_durable_structured_capabilities(harness)
        except DurableStructuredAdmissionError as exc:
            message = str(exc)
            errors.append(message)
            add(
                "invocation_mode",
                status=AgentOptionStatus.UNSUPPORTED,
                requested=profile.invocation_mode,
                effective=None,
                source="structured_capability_admission",
                detail=message,
            )
        else:
            adapter_options["execution_transport"] = (
                ExecutionTransport.NATIVE_STRUCTURED.value
            )
            add(
                "invocation_mode",
                status=AgentOptionStatus.EFFECTIVE,
                requested=profile.invocation_mode,
                effective=ExecutionTransport.NATIVE_STRUCTURED.value,
                source="structured_capability_admission",
                detail=(
                    "The durable worker uses the capability-proven structured "
                    f"{capabilities.protocol} driver."
                ),
            )
    for name, requested, effective, source, detail in (
        (
            "mode",
            profile.mode,
            profile.mode,
            "harness_policy_and_adapter_cli",
            "Harness policy and the adapter permission mode both receive this value.",
        ),
        (
            "workspace_policy",
            profile.workspace_policy,
            profile.workspace_policy,
            "harness_workspace",
            "Harness prepares the effective workspace before process spawn.",
        ),
        (
            "permission_profile",
            profile.permission_profile,
            profile.permission_profile,
            "harness_policy",
            "The durable dispatcher resolves this named approval policy.",
        ),
    ):
        add(
            name,
            status=AgentOptionStatus.EFFECTIVE,
            requested=requested,
            effective=effective,
            source=source,
            detail=detail,
        )

    requested_timeout = profile.budgets.timeout_seconds
    add(
        "budgets.timeout_seconds",
        status=AgentOptionStatus.EFFECTIVE,
        requested=requested_timeout,
        effective=(
            requested_timeout
            if requested_timeout is not None
            else default_timeout_seconds or "runtime_default"
        ),
        source="durable_job_monitor",
        detail="The worker cancels the attempt after the effective wall-clock timeout.",
    )
    add(
        "budgets.max_attempts",
        status=AgentOptionStatus.EFFECTIVE,
        requested=profile.budgets.max_attempts,
        effective=profile.budgets.max_attempts,
        source="durable_job_retry",
        detail="The coordination store caps logical job attempts at this value.",
    )
    if profile.budgets.max_concurrency == 1:
        add(
            "budgets.max_concurrency",
            status=AgentOptionStatus.EFFECTIVE,
            requested=1,
            effective=1,
            source="single_agent_run",
            detail="A standalone AgentProfile run owns one durable child at a time.",
        )
    else:
        message = (
            "AgentProfile max_concurrency above 1 requires a Workflow or Schedule "
            "coordinator and cannot be applied to a standalone agent run."
        )
        errors.append(message)
        add(
            "budgets.max_concurrency",
            status=AgentOptionStatus.UNSUPPORTED,
            requested=profile.budgets.max_concurrency,
            effective=None,
            source="unsupported",
            detail=message,
        )
    if profile.budgets.max_tokens is None:
        add(
            "budgets.max_tokens",
            status=AgentOptionStatus.EFFECTIVE,
            requested=None,
            effective=None,
            source="not_requested",
            detail="No token limit was requested.",
        )
    else:
        message = (
            f"{profile.harness_id} does not expose a version-proven headless token "
            "limit; max_tokens cannot be enforced."
        )
        errors.append(message)
        add(
            "budgets.max_tokens",
            status=AgentOptionStatus.UNSUPPORTED,
            requested=profile.budgets.max_tokens,
            effective=None,
            source="unsupported",
            detail=message,
        )

    probe = None
    needs_probe = bool(
        profile.reasoning_effort or profile.allowed_tools or profile.disallowed_tools
    )
    if needs_probe:
        capability_probe = getattr(harness, "capability_probe", None)
        probe = capability_probe() if callable(capability_probe) else None
    probe_capabilities = (
        dict(probe.capabilities) if probe is not None and probe.compatible else {}
    )
    reasoning_token = {
        "codex-cli": "--config",
        "claude-code": "--effort",
    }.get(profile.harness_id)
    supported_reasoning = {
        "codex-cli": {"none", "low", "medium", "high"},
        "claude-code": {"low", "medium", "high"},
    }.get(profile.harness_id, set())
    if profile.reasoning_effort is None:
        add(
            "reasoning_effort",
            status=AgentOptionStatus.EFFECTIVE,
            requested=None,
            effective=None,
            source="not_requested",
            detail="No reasoning effort override was requested.",
        )
    elif (
        reasoning_token is not None
        and profile.reasoning_effort in supported_reasoning
        and probe_capabilities.get(reasoning_token)
    ):
        adapter_options["reasoning_effort"] = profile.reasoning_effort
        add(
            "reasoning_effort",
            status=AgentOptionStatus.EFFECTIVE,
            requested=profile.reasoning_effort,
            effective=profile.reasoning_effort,
            source="adapter_cli",
            detail=(
                "Applied through a fixed adapter option proven by the installed "
                "CLI capability probe."
            ),
        )
    else:
        version = getattr(probe, "version", None) or "unproven version"
        message = (
            f"{profile.harness_id} {version} cannot apply reasoning_effort="
            f"{profile.reasoning_effort!r} through a proven adapter option."
        )
        errors.append(message)
        add(
            "reasoning_effort",
            status=AgentOptionStatus.UNSUPPORTED,
            requested=profile.reasoning_effort,
            effective=None,
            source="unsupported",
            detail=message,
        )

    for field_name, values, token in (
        ("allowed_tools", profile.allowed_tools, "--allowedTools"),
        ("disallowed_tools", profile.disallowed_tools, "--disallowedTools"),
    ):
        if not values:
            add(
                field_name,
                status=AgentOptionStatus.EFFECTIVE,
                requested=[],
                effective=[],
                source="not_requested",
                detail=f"No {field_name} restriction was requested.",
            )
        elif profile.harness_id == "claude-code" and probe_capabilities.get(token):
            adapter_options[field_name] = list(values)
            add(
                field_name,
                status=AgentOptionStatus.EFFECTIVE,
                requested=list(values),
                effective=list(values),
                source="adapter_cli",
                detail=(
                    "Applied through a fixed Claude Code tool restriction flag proven "
                    "by the installed CLI capability probe."
                ),
            )
        else:
            version = getattr(probe, "version", None) or "unproven version"
            message = (
                f"{profile.harness_id} {version} cannot apply {field_name} through "
                "a proven safe adapter option."
            )
            errors.append(message)
            add(
                field_name,
                status=AgentOptionStatus.UNSUPPORTED,
                requested=list(values),
                effective=None,
                source="unsupported",
                detail=message,
            )

    deferred_fields = (
        ("prompt_files", profile.prompt_files),
        ("skills", profile.skills),
        ("memory_selectors", profile.memory_selectors),
        ("context_selectors", profile.context_selectors),
    )
    for name, values in deferred_fields:
        if values:
            message = (
                f"{name} are preserved in provenance but are not materialized into "
                "the current headless adapter process."
            )
            warnings.append(message)
            add(
                name,
                status=AgentOptionStatus.UNSUPPORTED,
                requested=list(values),
                effective=None,
                source="provenance_only",
                detail=message,
            )
        else:
            add(
                name,
                status=AgentOptionStatus.EFFECTIVE,
                requested=[],
                effective=[],
                source="not_requested",
                detail=f"No {name} values were requested.",
            )
    if profile.tool_ids:
        if profile.invocation_mode == "headless" and profile.harness_id in {
            "codex-cli",
            "claude-code",
            "gemini-cli",
        }:
            add(
                "tool_ids",
                status=AgentOptionStatus.EFFECTIVE,
                requested=list(profile.tool_ids),
                effective=list(profile.tool_ids),
                source="managed_mcp_snapshot",
                detail=(
                    "Resolved to a trusted immutable project MCP snapshot before "
                    "queueing and materialized into the active temporary CLI home."
                ),
            )
        else:
            message = (
                "tool_ids require a built-in Codex, Claude, or Gemini headless "
                "adapter managed-MCP snapshot."
            )
            warnings.append(message)
            add(
                "tool_ids",
                status=AgentOptionStatus.UNSUPPORTED,
                requested=list(profile.tool_ids),
                effective=None,
                source="unsupported",
                detail=message,
            )
    else:
        add(
            "tool_ids",
            status=AgentOptionStatus.EFFECTIVE,
            requested=[],
            effective=[],
            source="not_requested",
            detail="No managed MCP servers were requested.",
        )
    add(
        "expected_artifact",
        status=(
            AgentOptionStatus.DELEGATED
            if profile.expected_artifact is not None
            else AgentOptionStatus.EFFECTIVE
        ),
        requested=profile.expected_artifact,
        effective=profile.expected_artifact,
        source=(
            "workflow_orchestration"
            if profile.expected_artifact is not None
            else "not_requested"
        ),
        detail=(
            "Workflow projection interprets this expected artifact; the external CLI "
            "does not enforce it."
            if profile.expected_artifact is not None
            else "No expected artifact contract was requested."
        ),
    )
    return AgentExecutionPlan(
        schema_version=1,
        harness_id=profile.harness_id,
        invocation_mode=profile.invocation_mode,
        options=options,
        adapter_options=adapter_options,
        errors=tuple(errors),
        warnings=tuple(warnings),
        binary_version=getattr(probe, "version", None),
        capability_evidence=getattr(probe, "evidence", None),
    )
