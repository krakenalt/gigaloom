"""Root dispatch contracts for declarative native agents and plain CLI."""

from __future__ import annotations

from dataclasses import replace
import subprocess
import sys

import pytest

from gigaloom import entrypoint
from gigaloom.cli_commands.launcher import (
    render_launcher_summary,
    render_root_help,
)
from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistry,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
)
from gigaloom.native.api import (
    NativeInvocation,
    NativeLaunchMode,
    NativeLaunchReason,
    TerminalContext,
    plan_native_launch,
)


PTY = TerminalContext(True, True, True, "xterm-256color", platform="darwin")
PIPE = TerminalContext(False, False, True, "xterm-256color", platform="darwin")


def _registry(*, include_fake: bool = False) -> AgentProfileRegistry:
    profiles = list(load_builtin_agent_profiles())
    if include_fake:
        codex = next(profile for profile in profiles if profile.agent_id == "codex")
        assert codex.native is not None
        profiles.append(
            replace(
                codex,
                agent_id="pi",
                display_name="Pi",
                aliases=("p",),
                native=replace(codex.native, executable_names=("pi",)),
            )
        )
    return AgentProfileRegistry.build(
        profiles,
        collision_contract=build_core_command_collision_contract(
            entrypoint._registered_core_commands()
        ),
    )


def _invocation(
    agent_id: str,
    suffix: tuple[str, ...],
    context: TerminalContext,
) -> NativeInvocation:
    return NativeInvocation(
        agent_id=agent_id,
        requested_token=agent_id,
        suffix=suffix,
        cwd="/workspace",
        stdin_is_tty=context.stdin_is_tty,
        stdout_is_tty=context.stdout_is_tty,
        stderr_is_tty=context.stderr_is_tty,
        ci=context.ci,
        platform=context.platform,
    )


def test_dynamic_profile_and_alias_route_without_provider_hardcoding(monkeypatch):
    registry = _registry(include_fake=True)
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "run_native_namespace",
        lambda arguments, **kwargs: calls.append((arguments, kwargs)) or 41,
    )

    assert (
        entrypoint.main(["p", "--future", "value"], context=PTY, registry=registry)
        == 41
    )

    arguments, kwargs = calls[0]
    assert arguments == ["p", "--future", "value"]
    assert kwargs["registry"].get("pi").native.executable_names == ("pi",)
    assert kwargs["context"] is PTY


def test_codex_root_resolves_the_declarative_native_profile(monkeypatch):
    registry = _registry()
    calls = []

    def run_native(arguments, **kwargs):
        profile = kwargs["registry"].get("codex")
        assert profile.native is not None
        calls.append((arguments, profile.native.executable_names))
        return 29

    monkeypatch.setattr(entrypoint, "run_native_namespace", run_native)

    assert entrypoint.main(["codex", "--help"], context=PTY, registry=registry) == 29
    assert calls == [(["codex", "--help"], ("codex",))]


@pytest.mark.parametrize("command", ("chat", "run", "session"))
def test_human_tty_core_commands_remain_plain_cli(command, monkeypatch):
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run_core_command",
        lambda arguments: calls.append(arguments) or 17,
    )
    monkeypatch.setattr(
        entrypoint,
        "run_native_namespace",
        lambda *_args, **_kwargs: pytest.fail("native route must not shadow core"),
    )

    assert (
        entrypoint.main([command, "fixture"], context=PTY, registry=_registry()) == 17
    )
    assert calls == [[command, "fixture"]]


def test_unknown_root_is_typed_and_has_bounded_suggestions(capsys):
    assert entrypoint.main(["codxe"], context=PIPE, registry=_registry()) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown command or agent 'codxe'" in captured.err
    assert "codex" in captured.err


def test_bare_entrypoint_is_plain_ansi_free_and_does_not_import_cli_or_tui(
    monkeypatch, capsys
):
    registry = AgentProfileRegistry.build(
        load_builtin_agent_profiles(),
        collision_contract=build_core_command_collision_contract(()),
    )
    monkeypatch.delitem(sys.modules, "gigaloom.cli", raising=False)
    monkeypatch.delitem(sys.modules, "gigaloom.tui.entrypoint", raising=False)

    assert entrypoint.main([], context=PIPE, registry=registry) == 0

    output = capsys.readouterr().out
    assert "GigaLoom " in output
    assert "Native agents" in output
    assert "codex" in output and "claude" in output and "gemini" in output
    assert "Open Web:       giga ui" in output
    assert "\x1b" not in output
    assert "gigaloom.cli" not in sys.modules
    assert "gigaloom.tui.entrypoint" not in sys.modules


def test_bare_entrypoint_does_not_import_terminal_control_modules():
    source = """
import contextlib
import io
import sys

from gigaloom import entrypoint

with contextlib.redirect_stdout(io.StringIO()):
    assert entrypoint.main([]) == 0
blocked = sorted(
    name
    for name in sys.modules
    if name in {'pty', 'termios', 'textual', 'tty'}
    or name.startswith('gigaloom.native.terminal')
)
print(','.join(blocked))
"""

    completed = subprocess.run(
        (sys.executable, "-c", source),
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout == "\n"


def test_launcher_summary_uses_path_lookup_without_provider_execution(monkeypatch):
    lookups = []
    monkeypatch.setattr(
        "gigaloom.cli_commands.launcher.shutil.which",
        lambda executable, **kwargs: lookups.append((executable, kwargs)) or None,
    )

    output = render_launcher_summary(
        load_builtin_agent_profiles(),
        environ={"PATH": "/fixture/bin"},
        platform="darwin",
    )

    assert [item[0] for item in lookups] == ["claude", "codex", "gemini"]
    assert all(item[1] == {"path": "/fixture/bin"} for item in lookups)
    assert output.count("not found") == 3


def test_root_help_is_static_and_teaches_native_vs_structured_split():
    help_text = render_root_help()

    assert "giga <agent-id-or-alias> [provider arguments...]" in help_text
    assert "giga run" in help_text
    assert "giga session" in help_text
    assert "Textual" not in help_text
    assert "tui" not in help_text.casefold()


@pytest.mark.parametrize(
    ("agent_id", "suffix", "context", "supported", "mode", "reason"),
    (
        (
            "codex",
            (),
            PTY,
            True,
            NativeLaunchMode.MANAGED_NATIVE,
            NativeLaunchReason.AFFIRMATIVE_HUMAN_TTY,
        ),
        (
            "codex",
            ("--help",),
            PTY,
            True,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.METADATA_FORM,
        ),
        (
            "codex",
            ("exec", "--json", "inspect"),
            PTY,
            True,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.HEADLESS_FORM,
        ),
        (
            "codex",
            ("--help",),
            PTY,
            True,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.METADATA_FORM,
        ),
        (
            "claude",
            ("-c",),
            PIPE,
            True,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.NON_INTERACTIVE_TOPOLOGY,
        ),
        (
            "gemini",
            ("-i", "inspect", "--future"),
            PTY,
            True,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.UNKNOWN_FORM,
        ),
        (
            "gemini",
            (),
            PTY,
            False,
            NativeLaunchMode.DIRECT_NATIVE,
            NativeLaunchReason.MANAGED_TERMINAL_DISABLED,
        ),
    ),
)
def test_native_planner_is_affirmative_and_machine_forms_stay_direct(
    agent_id, suffix, context, supported, mode, reason
):
    profile = next(
        profile
        for profile in load_builtin_agent_profiles()
        if profile.agent_id == agent_id
    )
    assert profile.native is not None

    plan = plan_native_launch(
        _invocation(agent_id, suffix, context),
        profile.native,
        managed_terminal_supported=supported,
    )

    assert plan.mode is mode
    assert plan.reason is reason
