"""Static shell completion for the stable Harness command boundary."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final


SHELLS: Final = ("bash", "zsh", "fish", "powershell")

_CORE_ROOT_COMMANDS = (
    "agent bootstrap chat completion config doctor eval handoff harness init integration memory "
    "native open preset project provider run runtime schedule session state tui "
    "ui worker workflow"
)
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_COMPLETION_AGENTS = 1_000
_ROOT_COMMANDS_MARKER = "__GIGALOOM_ROOT_COMMANDS__"

_SCRIPTS: Final = {
    "bash": f"""# giga completion for Bash
_giga_complete() {{
    local current="${{COMP_WORDS[COMP_CWORD]}}"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W "{_ROOT_COMMANDS_MARKER}" -- "$current") )
    else
        COMPREPLY=()
    fi
}}
complete -o default -F _giga_complete giga
""",
    "zsh": f"""#compdef giga
_giga() {{
  if (( CURRENT == 2 )); then
    compadd -- {_ROOT_COMMANDS_MARKER}
  else
    _default
  fi
}}
compdef _giga giga
""",
    "fish": f"""# giga completion for Fish
complete -c giga -f -n '__fish_use_subcommand' -a '{_ROOT_COMMANDS_MARKER}'
""",
    "powershell": f"""# giga completion for PowerShell
Register-ArgumentCompleter -Native -CommandName giga -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    if ($commandAst.CommandElements.Count -eq 2) {{
        '{_ROOT_COMMANDS_MARKER}'.Split(' ') |
            Where-Object {{ $_ -like "$wordToComplete*" }} |
            ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }}
    }}
}}
""",
}


def render_completion(
    shell: str,
    *,
    agent_ids: Iterable[str] | None = None,
) -> str:
    """Render completion without parsing or mirroring provider-owned suffixes."""
    try:
        template = _SCRIPTS[shell]
    except KeyError as exc:  # pragma: no cover - argparse owns public validation.
        raise ValueError(f"Unsupported shell: {shell}") from exc
    return template.replace(
        _ROOT_COMMANDS_MARKER,
        " ".join(root_completion_candidates(agent_ids=agent_ids)),
    )


def root_completion_candidates(
    *,
    agent_ids: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return bounded core commands plus declarative Agent Profile ids."""
    if agent_ids is None:
        from gigaloom.harnesses.agent_profiles import load_builtin_agent_profiles

        selected = tuple(profile.agent_id for profile in load_builtin_agent_profiles())
    else:
        selected = tuple(agent_ids)
    if len(selected) > _MAX_COMPLETION_AGENTS:
        raise ValueError("agent completion source is too large")
    core = tuple(_CORE_ROOT_COMMANDS.split())
    core_set = frozenset(core)
    for agent_id in selected:
        if not isinstance(agent_id, str) or _IDENTITY_RE.fullmatch(agent_id) is None:
            raise ValueError("agent completion id is invalid")
        if agent_id in core_set:
            raise ValueError("agent completion id collides with a core command")
    return (*core, *tuple(sorted(set(selected))))
