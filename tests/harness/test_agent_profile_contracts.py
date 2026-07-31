"""Contracts for declarative agent identities and native launch planning."""

from __future__ import annotations

import argparse
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import tomllib

import pytest

from gigaloom.cli_commands.parser import build_parser
from gigaloom.harnesses.agent_profiles import (
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    CoreCommandCollisionContractV1,
    RELEASE_RESERVED_CORE_COMMANDS,
    load_builtin_agent_profiles,
)
from gigaloom.harnesses.agent_profiles.manifests import (
    decode_agent_profile_manifest,
)
from gigaloom.native.launch import (
    AgentResolutionKind,
    AgentResolutionReason,
    AgentResolutionResult,
    NativeInvocation,
    NativeLaunchMode,
    NativeLaunchReason,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"


def test_builtin_profiles_load_from_reviewed_declarative_resources() -> None:
    profiles = load_builtin_agent_profiles()

    assert [profile.agent_id for profile in profiles] == ["claude", "codex", "gemini"]
    expected_routes = {
        "codex": ("codex.app-server", "codex_app_server_v1"),
        "claude": ("claude.stream-json", "claude_stream_json_v1"),
        "gemini": ("gemini.acp", "acp_stdio_v1"),
    }
    resources = files("gigaloom.harnesses.agent_profiles.builtins")
    for profile in profiles:
        resource = resources.joinpath(f"{profile.agent_id}.toml")
        assert profile.profile_digest == sha256(resource.read_bytes()).hexdigest()
        assert profile.source.digest == profile.profile_digest
        assert profile.source.kind is AgentProfileSourceKind.BUILTIN
        assert profile.source.trust_class is AgentProfileTrustClass.FIRST_PARTY
        assert profile.source.reviewed is True
        assert profile.aliases == ()
        assert profile.native is not None
        assert not hasattr(profile.native, "default_args")
        assert profile.native.executable_names == (profile.agent_id,)
        assert profile.native.supports_managed_terminal is True
        assert len(profile.structured_routes) == 1
        route = profile.structured_routes[0]
        assert (route.route_id, route.transport_kind) == expected_routes[
            profile.agent_id
        ]
        assert route.route_id != profile.agent_id
        assert route.compatibility_profile_id in {
            item.compatibility_profile_id for item in profile.compatibility_profiles
        }
        with pytest.raises(FrozenInstanceError):
            profile.agent_id = "other"


def test_builtin_loading_does_not_import_provider_implementations() -> None:
    import subprocess
    import sys
    import textwrap

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            textwrap.dedent(
                """
                import sys
                from gigaloom.harnesses.agent_profiles import (
                    load_builtin_agent_profiles,
                )

                assert len(load_builtin_agent_profiles()) == 3
                forbidden = (
                    "gigaloom.harnesses.builtins.codex",
                    "gigaloom.harnesses.builtins.claude",
                    "gigaloom.harnesses.builtins.gemini",
                )
                assert not any(
                    name == prefix or name.startswith(prefix + ".")
                    for name in sys.modules
                    for prefix in forbidden
                )
                """
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_core_collision_fixture_is_derived_from_the_real_parser() -> None:
    payload = json.loads((FIXTURES / "core_commands.json").read_text(encoding="utf-8"))
    parser = build_parser()
    action = next(
        item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
    )

    assert payload["source"] == "gigaloom.cli_commands.parser.build_parser"
    assert payload["registered_commands"] == sorted(action.choices)
    assert tuple(payload["release_reserved_commands"]) == (
        RELEASE_RESERVED_CORE_COMMANDS
    )
    contract = CoreCommandCollisionContractV1(
        schema_version=payload["schema_version"],
        registered_commands=tuple(payload["registered_commands"]),
        release_reserved_commands=tuple(payload["release_reserved_commands"]),
    )
    assert {"route", "capsule", "run", "ui", "harness"} <= (contract.blocked_commands)
    for profile in load_builtin_agent_profiles():
        contract.validate_profile(profile)

    collision = replace(load_builtin_agent_profiles()[0], aliases=("run",))
    assert contract.collisions(collision) == ("run",)
    with pytest.raises(ValueError, match="collides with core commands: run"):
        contract.validate_profile(collision)


def test_native_invocation_preserves_the_opaque_suffix_only_in_memory() -> None:
    suffix = ("--model", "provider-owned", "", "Привет", "line one\nline two")
    invocation = NativeInvocation(
        agent_id="codex",
        requested_token="codex",
        suffix=suffix,
        cwd="/workspace with spaces",
        stdin_is_tty=True,
        stdout_is_tty=True,
        stderr_is_tty=True,
        ci=False,
        platform="darwin",
    )

    assert invocation.suffix == suffix
    with pytest.raises(FrozenInstanceError):
        invocation.suffix = ()

    resolution = AgentResolutionResult(
        kind=AgentResolutionKind.AGENT_ALIAS,
        reason=AgentResolutionReason.AGENT_ALIAS_MATCH,
        requested_token="openai-codex",
        agent_id="codex",
        profile_digest="a" * 64,
        matched_alias="openai-codex",
    )
    assert not hasattr(resolution, "suffix")
    assert not hasattr(resolution, "argv")


def test_resolution_invariants_and_bounded_unknown_suggestions_fail_closed() -> None:
    core = AgentResolutionResult(
        kind=AgentResolutionKind.CORE_COMMAND,
        reason=AgentResolutionReason.CORE_COMMAND_RESERVED,
        requested_token="run",
        core_command="run",
    )
    unknown = AgentResolutionResult(
        kind=AgentResolutionKind.UNKNOWN,
        reason=AgentResolutionReason.UNKNOWN_COMMAND_OR_AGENT,
        requested_token="codxe",
        suggestions=("codex", "config"),
    )

    assert core.agent_id is None
    assert unknown.suggestions == ("codex", "config")
    with pytest.raises(ValueError, match="reason does not match"):
        replace(core, reason=AgentResolutionReason.AGENT_ID_MATCH)
    with pytest.raises(ValueError, match="suggestions are invalid"):
        replace(unknown, suggestions=("a", "b", "c", "d", "e", "f"))


def test_collision_contract_rejects_noncanonical_or_overlapping_lists() -> None:
    with pytest.raises(ValueError, match="must be sorted"):
        CoreCommandCollisionContractV1(
            registered_commands=("run", "agent"),
            release_reserved_commands=(),
        )
    with pytest.raises(ValueError, match="must be disjoint"):
        CoreCommandCollisionContractV1(
            registered_commands=("agent", "run"),
            release_reserved_commands=("run",),
        )


def test_manifest_decoder_rejects_unknown_fields_and_unbound_compatibility() -> None:
    resource = files("gigaloom.harnesses.agent_profiles.builtins").joinpath(
        "codex.toml"
    )
    payload = resource.read_bytes()
    document = tomllib.loads(payload.decode("utf-8"))
    document["credential"] = "must-not-be-accepted"

    with pytest.raises(ValueError, match="unknown=\['credential'\]"):
        decode_agent_profile_manifest(
            document,
            manifest_digest=sha256(payload).hexdigest(),
        )

    profile = load_builtin_agent_profiles()[1]
    broken_route = replace(
        profile.structured_routes[0],
        compatibility_profile_id="missing.profile",
    )
    with pytest.raises(ValueError, match="unknown compatibility profile"):
        replace(profile, structured_routes=(broken_route,))


def test_stable_native_route_and_resolution_enum_values() -> None:
    assert {item.value for item in NativeLaunchMode} == {
        "direct_native",
        "managed_native",
    }
    assert {item.value for item in NativeLaunchReason} == {
        "affirmative_human_tty",
        "metadata_form",
        "headless_form",
        "non_interactive_topology",
        "ci_environment",
        "unsupported_platform",
        "managed_terminal_disabled",
        "unknown_form",
    }
    assert [item.value for item in AgentResolutionKind] == [
        "root_metadata",
        "core_command",
        "agent_id",
        "agent_alias",
        "unknown",
    ]
