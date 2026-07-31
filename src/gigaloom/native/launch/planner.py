"""Deterministic direct-versus-managed native launch planning."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.native.launch.contracts import (
    NativeAgentLaunchSpec,
    NativeIntentMatcher,
    NativeIntentMatcherKind,
    NativeInvocation,
    NativeLaunchMode,
    NativeLaunchReason,
)


_MANAGED_PLATFORMS = frozenset({"darwin", "linux"})


@dataclass(frozen=True)
class NativeLaunchPlan:
    """Content-free launch decision with no structured route authority."""

    mode: NativeLaunchMode
    reason: NativeLaunchReason
    matched_intent_id: str | None = None


def plan_native_launch(
    invocation: NativeInvocation,
    spec: NativeAgentLaunchSpec,
    *,
    managed_terminal_supported: bool,
) -> NativeLaunchPlan:
    """Choose managed mode only for one affirmative human-native form."""
    metadata = _first_match(spec.metadata_matchers, invocation.suffix)
    if metadata is not None:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.METADATA_FORM,
            metadata.matcher_id,
        )
    headless = _first_match(spec.headless_matchers, invocation.suffix)
    if headless is not None:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.HEADLESS_FORM,
            headless.matcher_id,
        )
    interactive = _first_match(spec.interactive_matchers, invocation.suffix)
    if interactive is None:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.UNKNOWN_FORM,
        )
    if invocation.ci:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.CI_ENVIRONMENT,
            interactive.matcher_id,
        )
    if not (
        invocation.stdin_is_tty
        and invocation.stdout_is_tty
        and invocation.stderr_is_tty
    ):
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.NON_INTERACTIVE_TOPOLOGY,
            interactive.matcher_id,
        )
    if invocation.platform not in _MANAGED_PLATFORMS:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.UNSUPPORTED_PLATFORM,
            interactive.matcher_id,
        )
    if not spec.supports_managed_terminal or not managed_terminal_supported:
        return NativeLaunchPlan(
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.MANAGED_TERMINAL_DISABLED,
            interactive.matcher_id,
        )
    return NativeLaunchPlan(
        NativeLaunchMode.MANAGED_NATIVE,
        NativeLaunchReason.AFFIRMATIVE_HUMAN_TTY,
        interactive.matcher_id,
    )


def _first_match(
    matchers: tuple[NativeIntentMatcher, ...],
    suffix: tuple[str, ...],
) -> NativeIntentMatcher | None:
    return next((matcher for matcher in matchers if _matches(matcher, suffix)), None)


def _matches(matcher: NativeIntentMatcher, suffix: tuple[str, ...]) -> bool:
    if matcher.kind is NativeIntentMatcherKind.EMPTY_SUFFIX:
        return not suffix
    if matcher.kind is NativeIntentMatcherKind.SINGLE_POSITIONAL:
        return len(suffix) == 1 and not suffix[0].startswith("-")
    if matcher.kind is NativeIntentMatcherKind.EXACT_SUFFIX:
        return suffix == matcher.tokens
    if matcher.kind is NativeIntentMatcherKind.FIRST_TOKEN:
        return bool(suffix) and suffix[0] in matcher.tokens
    if matcher.kind is NativeIntentMatcherKind.FIRST_TOKEN_WITH_VALUE:
        return len(suffix) == 2 and suffix[0] in matcher.tokens
    if matcher.kind is NativeIntentMatcherKind.ANY_OPTION:
        return any(token in matcher.tokens for token in suffix)
    raise AssertionError(f"unhandled native matcher kind: {matcher.kind.value}")
