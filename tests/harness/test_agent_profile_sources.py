"""Source-bound discovery contracts for declarative Agent Profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from gigaloom.harnesses.agent_profiles import (
    AGENT_PROFILE_ENTRY_POINT_GROUP,
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    decode_registry_distribution_candidates,
    discover_installed_agent_profile_candidates,
    discover_local_agent_profiles,
    load_local_agent_profile,
)
from gigaloom.harnesses.agent_profiles.codec import (
    MAX_AGENT_PROFILE_MANIFEST_BYTES,
    decode_agent_profile_toml,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"


@dataclass
class _Distribution:
    name: str = "fixture-plugin"
    version: str = "1.2.3"

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}


@dataclass
class _EntryPoint:
    name: str
    value: str
    group: str = AGENT_PROFILE_ENTRY_POINT_GROUP
    dist: _Distribution | None = None
    load_calls: int = 0

    def load(self) -> object:
        self.load_calls += 1
        raise AssertionError("entry-point discovery must not import plugin code")


def test_local_manifest_is_bounded_and_bound_to_the_real_source(tmp_path: Path) -> None:
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes((FIXTURES / "test-agent.toml").read_bytes())

    profile = load_local_agent_profile(manifest)

    assert profile.agent_id == "test-agent"
    assert profile.aliases == ("ta",)
    assert profile.source.kind is AgentProfileSourceKind.LOCAL_MANIFEST
    assert profile.source.trust_class is AgentProfileTrustClass.LOCAL
    assert profile.source.reviewed is False
    assert profile.source.origin == str(manifest.resolve())
    assert profile.source.digest == profile.profile_digest


def test_local_manifest_cannot_self_assert_builtin_or_reviewed(tmp_path: Path) -> None:
    payload = (FIXTURES / "test-agent.toml").read_text(encoding="utf-8")
    builtin = tmp_path / "builtin.toml"
    builtin.write_text(
        payload.replace('kind = "local_manifest"', 'kind = "builtin"'),
        encoding="utf-8",
    )
    reviewed = tmp_path / "reviewed.toml"
    reviewed.write_text(
        payload.replace("reviewed = false", "reviewed = true"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source kind local_manifest"):
        load_local_agent_profile(builtin)
    with pytest.raises(ValueError, match="self-assert reviewed"):
        load_local_agent_profile(reviewed)


def test_local_discovery_is_sorted_bounded_and_isolates_invalid_siblings(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "test-agent.toml").read_bytes()
    (tmp_path / "b.toml").write_bytes(payload)
    (tmp_path / "a.toml").write_text("not = [valid", encoding="utf-8")

    discovery = discover_local_agent_profiles(tmp_path)

    assert [profile.agent_id for profile in discovery.profiles] == ["test-agent"]
    assert len(discovery.errors) == 1
    assert discovery.errors[0].source.endswith("a.toml")
    assert discovery.errors[0].code == "invalid_manifest"


def test_local_loader_rejects_symlinks_directories_and_oversized_payloads(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes((FIXTURES / "test-agent.toml").read_bytes())
    link = tmp_path / "linked.toml"
    link.symlink_to(manifest)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        load_local_agent_profile(link)
    with pytest.raises(ValueError, match="regular file"):
        load_local_agent_profile(tmp_path)
    with pytest.raises(ValueError, match="too large"):
        decode_agent_profile_toml(b"x" * (MAX_AGENT_PROFILE_MANIFEST_BYTES + 1))


def test_installed_entry_point_discovery_never_loads_plugin_code() -> None:
    entry_point = _EntryPoint(
        name="fixture-agent",
        value="fixture_package.profile:manifest",
        dist=_Distribution(),
    )

    candidates = discover_installed_agent_profile_candidates((entry_point,))

    assert entry_point.load_calls == 0
    assert len(candidates) == 1
    assert candidates[0].distribution_name == "fixture-plugin"
    assert candidates[0].distribution_version == "1.2.3"
    assert len(candidates[0].metadata_digest) == 64


def test_registry_install_metadata_with_shell_syntax_stays_inert_data() -> None:
    records = (
        {
            "distribution_id": "fixture.npx",
            "kind": "npx",
            "package": "@fixture/agent; touch should-not-run",
            "version": "1.0.0",
            "executable": "fixture && echo should-not-run",
        },
        {
            "distribution_id": "fixture.uvx",
            "kind": "uvx",
            "package": "fixture-agent[cli]",
            "version": "2.0.0",
            "executable": None,
        },
    )

    candidates = decode_registry_distribution_candidates(records)

    assert candidates[0].package.endswith("touch should-not-run")
    assert candidates[0].executable == "fixture && echo should-not-run"
    assert candidates[1].distribution_kind == "uvx"
    assert not hasattr(candidates[0], "command")
