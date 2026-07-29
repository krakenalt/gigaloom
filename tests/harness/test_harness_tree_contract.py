"""Structural contracts for provider-grouped built-in harness adapters."""

from __future__ import annotations

import importlib
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).parents[2]
HARNESS_ROOT = (
    ROOT / "packages" / "gpt2giga-harness" / "src" / "gpt2giga_harness" / "harnesses"
)

LEGACY_ALIASES = {
    "adapter_parity": "sdk.adapter_parity",
    "agent_cli": "sdk.agent_cli",
    "attachment_plan": "sdk.attachment_plan",
    "base": "sdk.base",
    "contracts": "sdk.contracts",
    "direct_chat": "builtins.direct_chat.adapter",
    "codex_cli": "builtins.codex.cli",
    "codex_workbench": "builtins.codex.workbench",
    "claude_code": "builtins.claude.cli",
    "claude_workbench": "builtins.claude.workbench",
    "gemini_cli": "builtins.gemini.cli",
    "gemini_workbench": "builtins.gemini.workbench",
    "echo": "builtins.echo",
}

PLUGIN_TARGETS = {
    "direct-chat": "gpt2giga_harness.harnesses.direct_chat:DirectChatHarness",
    "codex-cli": "gpt2giga_harness.harnesses.codex_cli:CodexCliHarness",
    "claude-code": "gpt2giga_harness.harnesses.claude_code:ClaudeCodeHarness",
    "gemini-cli": "gpt2giga_harness.harnesses.gemini_cli:GeminiCliHarness",
    "echo": "gpt2giga_harness.harnesses.echo:EchoHarness",
}


@pytest.mark.parametrize(("legacy", "implementation"), LEGACY_ALIASES.items())
def test_legacy_harness_modules_are_exact_aliases(
    legacy: str,
    implementation: str,
) -> None:
    prefix = "gpt2giga_harness.harnesses."
    assert importlib.import_module(prefix + legacy) is importlib.import_module(
        prefix + implementation
    )


def test_plugin_entry_points_retain_stable_targets() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = metadata["project"]["entry-points"]
    assert entry_points["gpt2giga.harnesses"] == PLUGIN_TARGETS
    assert entry_points["agent_workbench.harness_adapters.v1"] == PLUGIN_TARGETS


@pytest.mark.parametrize(
    "relative_path",
    [
        "sdk/agent_cli.py",
        "sdk/_agent_cli_internal.py",
        "sdk/events.py",
        "builtins/direct_chat/adapter.py",
        "builtins/direct_chat/payloads.py",
        "builtins/direct_chat/tools.py",
        "builtins/claude/cli.py",
    ],
)
def test_c2_implementation_modules_respect_size_budget(relative_path: str) -> None:
    line_count = len(
        (HARNESS_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
    )
    assert line_count <= 600


@pytest.mark.parametrize("legacy", LEGACY_ALIASES)
def test_legacy_harness_modules_remain_thin(legacy: str) -> None:
    line_count = len(
        (HARNESS_ROOT / f"{legacy}.py").read_text(encoding="utf-8").splitlines()
    )
    assert line_count <= 30
