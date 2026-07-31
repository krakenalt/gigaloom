"""Deterministic soft launch profile resolution."""

from __future__ import annotations

from .models import (
    LaunchResolutionContextV1,
    ProjectLaunchProfileV1,
    ResolvedProjectLaunchProfileV1,
    UnsatisfiedLaunchHintV1,
)


def resolve_launch_profile(
    profile: ProjectLaunchProfileV1,
    context: LaunchResolutionContextV1,
) -> ResolvedProjectLaunchProfileV1:
    """Resolve only known hints and expose every unavailable value."""
    unsatisfied: list[UnsatisfiedLaunchHintV1] = []
    agent = _resolve("agent_hint", profile.agent_hint, context.agent_ids, unsatisfied)
    route = _resolve(
        "structured_route_hint",
        profile.structured_route_hint,
        context.structured_route_ids,
        unsatisfied,
    )
    model = _resolve("model_hint", profile.model_hint, context.model_ids, unsatisfied)
    mode = _resolve("mode_hint", profile.mode_hint, context.modes, unsatisfied)
    host = _resolve("host_hint", profile.host_hint, context.host_ids, unsatisfied)
    workspace_policy = _resolve(
        "workspace_policy_hint",
        profile.workspace_policy_hint,
        context.workspace_policies,
        unsatisfied,
    )
    terminal_mode = profile.terminal_mode_hint
    if terminal_mode == "auto":
        resolved_terminal_mode = terminal_mode
    else:
        resolved_terminal_mode = _resolve(
            "terminal_mode_hint",
            terminal_mode,
            context.terminal_modes,
            unsatisfied,
        )
    return ResolvedProjectLaunchProfileV1(
        launch_profile_id=profile.launch_profile_id,
        catalog_project_id=profile.catalog_project_id,
        profile_digest=profile.digest,
        agent_id=agent,
        structured_route_id=route,
        model_id=model,
        mode=mode,
        host_id=host,
        workspace_policy=workspace_policy,
        terminal_mode=resolved_terminal_mode,
        unsatisfied_hints=tuple(unsatisfied),
    )


def _resolve(
    field: str,
    value: str | None,
    available: frozenset[str],
    unsatisfied: list[UnsatisfiedLaunchHintV1],
) -> str | None:
    if value is None:
        return None
    if value in available:
        return value
    unsatisfied.append(UnsatisfiedLaunchHintV1(field=field, value=value))
    return None
