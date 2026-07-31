"""Import-light plain-text launcher and root help rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import shutil
import sys

from gigaloom import __version__
from gigaloom.harnesses.agent_profiles import AgentProfileV1


def render_root_help() -> str:
    """Return static root help without importing runtime or presentation code."""
    return """usage: giga [--help] [--version]
       giga <core-command> [arguments...]
       giga <agent-id-or-alias> [provider arguments...]

GigaLoom native-agent launcher and Web control plane.

Native agents:
  giga codex [args...]     Launch the real Codex CLI
  giga claude [args...]    Launch the real Claude Code CLI
  giga gemini [args...]    Launch the real Gemini CLI

Main commands:
  giga ui                  Open the Web control plane
  giga agent ...           Inspect declarative agent profiles
  giga project ...         Manage projects and launch profiles
  giga run ...             Start an explicit structured run
  giga session ...         Inspect or mutate sessions as plain CLI
  giga completion <shell>  Generate shell completion

Run 'giga --non-interactive --help' for the complete admin command inventory.
Provider arguments after an agent token are passed through unchanged.
"""


def render_launcher_summary(
    profiles: Sequence[AgentProfileV1],
    *,
    facade_executable: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str:
    """Render bounded PATH-only agent readiness without executing providers."""
    snapshot = tuple(sorted(profiles, key=lambda item: item.agent_id))
    environment = os.environ if environ is None else environ
    effective_platform = sys.platform if platform is None else platform
    width = max((len(profile.agent_id) for profile in snapshot), default=1)
    lines = [f"GigaLoom {__version__}", "", "Native agents"]
    for profile in snapshot:
        lines.append(
            f"  {profile.agent_id:<{width}}  "
            f"{_profile_status(profile, environment, effective_platform, facade_executable)}"
        )
    lines.extend(
        (
            "",
            "Open Web:       giga ui",
            "List projects:  giga project list",
            "Inspect agents: giga agent list",
            "Launch agent:   giga <agent>",
        )
    )
    return "\n".join(lines) + "\n"


def _profile_status(
    profile: AgentProfileV1,
    environment: Mapping[str, str],
    platform: str,
    facade_executable: str | os.PathLike[str] | None,
) -> str:
    if platform not in profile.platform_support:
        return "unsupported"
    if profile.native is None:
        return "structured only"
    search_path = environment.get("PATH")
    for executable in profile.native.executable_names:
        candidate = shutil.which(executable, path=search_path)
        if candidate is None:
            continue
        if facade_executable is not None:
            try:
                if os.path.samefile(candidate, facade_executable):
                    return "unavailable"
            except OSError:
                return "unavailable"
        return "ready"
    return "not found"
