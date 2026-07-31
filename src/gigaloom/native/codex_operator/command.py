"""Lightweight argument contract for the future `giga codex` root hook."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.native.codex_operator.session_contracts import CodexCwdDecision


@dataclass(frozen=True)
class CodexOperatorIntent:
    """Wrapper-owned arguments separated from raw Codex-owned arguments."""

    resume_binding_id: str | None
    fresh: bool
    cwd_decision: CodexCwdDecision | None
    provider_args: tuple[str, ...]


def parse_codex_operator_args(arguments: tuple[str, ...]) -> CodexOperatorIntent:
    """Parse only reviewed wrapper flags before the optional `--` boundary."""
    wrapper, separator, provider = _partition(arguments)
    resume_binding_id: str | None = None
    fresh = False
    cwd_decision: CodexCwdDecision | None = None
    index = 0
    while index < len(wrapper):
        argument = wrapper[index]
        if argument == "--resume":
            index += 1
            if index >= len(wrapper) or resume_binding_id is not None:
                raise ValueError("giga codex --resume requires one binding id")
            resume_binding_id = wrapper[index]
        elif argument == "--fresh":
            fresh = True
        elif argument == "--use-bound-cwd":
            if cwd_decision is not None:
                raise ValueError("giga codex cwd decision is ambiguous")
            cwd_decision = CodexCwdDecision.BOUND
        elif argument == "--use-current-cwd":
            if cwd_decision is not None:
                raise ValueError("giga codex cwd decision is ambiguous")
            cwd_decision = CodexCwdDecision.CURRENT
        else:
            if separator:
                raise ValueError(f"unknown giga codex option: {argument}")
            return CodexOperatorIntent(None, False, None, arguments)
        index += 1
    if resume_binding_id is None and (fresh or cwd_decision is not None):
        raise ValueError("giga codex resume options require --resume")
    return CodexOperatorIntent(
        resume_binding_id=resume_binding_id,
        fresh=fresh,
        cwd_decision=cwd_decision,
        provider_args=provider,
    )


def _partition(
    arguments: tuple[str, ...],
) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
    try:
        boundary = arguments.index("--")
    except ValueError:
        return arguments, False, ()
    return arguments[:boundary], True, arguments[boundary + 1 :]
