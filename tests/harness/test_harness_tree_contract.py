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

PROVIDER_LEGACY_ALIASES = {
    "claude_agent_sdk": "harnesses.builtins.claude.sdk",
    "claude_handoff": "harnesses.builtins.claude.handoff",
    "claude_plugin_target": "harnesses.builtins.claude.target",
    "gemini_acp": "harnesses.builtins.gemini.acp",
    "gemini_extension_target": "harnesses.builtins.gemini.target",
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
    ("legacy", "implementation"),
    PROVIDER_LEGACY_ALIASES.items(),
)
def test_provider_specific_legacy_modules_are_exact_aliases(
    legacy: str,
    implementation: str,
) -> None:
    prefix = "gpt2giga_harness."
    assert importlib.import_module(prefix + legacy) is importlib.import_module(
        prefix + implementation
    )


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
        "builtins/codex/cli.py",
        "builtins/codex/config.py",
        "builtins/codex/streaming.py",
        "builtins/codex/app_server/approvals.py",
        "builtins/codex/app_server/contracts.py",
        "builtins/codex/app_server/driver.py",
        "builtins/codex/app_server/links.py",
        "builtins/codex/app_server/process.py",
        "builtins/codex/app_server/protocol.py",
        "builtins/codex/app_server/rollout.py",
        "builtins/codex/app_server/session.py",
        "builtins/codex/app_server/snapshots.py",
        "builtins/codex/app_server/utils.py",
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


def test_codex_app_server_compatibility_facade_remains_bounded() -> None:
    facade = (
        ROOT
        / "packages"
        / "gpt2giga-harness"
        / "src"
        / "gpt2giga_harness"
        / "codex_app_server.py"
    )
    assert len(facade.read_text(encoding="utf-8").splitlines()) <= 250


def test_codex_protocol_normalization_is_separate_from_process_lifecycle() -> None:
    app_server = HARNESS_ROOT / "builtins" / "codex" / "app_server"
    protocol_source = (app_server / "protocol.py").read_text(encoding="utf-8")
    process_source = (app_server / "process.py").read_text(encoding="utf-8")
    assert "import subprocess" not in protocol_source
    assert ".process import" not in protocol_source
    assert ".protocol import" not in process_source


def test_provider_specific_implementation_modules_respect_size_budget() -> None:
    provider_modules = (
        *sorted((HARNESS_ROOT / "builtins" / "claude").glob("*.py")),
        *sorted((HARNESS_ROOT / "builtins" / "gemini").glob("*.py")),
    )
    oversized = {
        path.relative_to(HARNESS_ROOT).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in provider_modules
        if len(path.read_text(encoding="utf-8").splitlines()) > 600
    }
    assert oversized == {}
