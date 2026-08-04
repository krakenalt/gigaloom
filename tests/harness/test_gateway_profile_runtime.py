"""Reviewed gpt2giga profile and installed-artifact runtime evidence."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

from gigaloom.native.launch import gateway_profile as profile_module
from gigaloom.native.launch.gateway_contracts import GatewayMode
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_WHEEL_SHA256,
    resolve_installed_gpt2giga_artifact,
    reviewed_gpt2giga_profile,
)


class _Distribution:
    version = "0.3.0"
    entry_points = (
        SimpleNamespace(
            group="console_scripts",
            name="gpt2giga",
            value="gpt2giga:run",
        ),
    )
    metadata = {"Name": "gpt2giga"}

    def __init__(self, payload: Path, *, direct_url: str | None = None) -> None:
        digest = hashlib.sha256(payload.read_bytes()).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.files = (
            SimpleNamespace(
                hash=SimpleNamespace(mode="sha256", value=encoded),
            ),
        )
        self.payload = payload
        self.direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self.direct_url

    def locate_file(self, _item: object) -> Path:
        return self.payload


def test_resolver_binds_registry_distribution_entrypoint_and_record_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = tmp_path / "installed.py"
    payload.write_text("VALUE = 1\n", encoding="utf-8")
    executable = tmp_path / "gpt2giga"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    installed = _Distribution(payload)
    monkeypatch.setattr(profile_module, "distribution", lambda _name: installed)
    monkeypatch.setattr(
        profile_module,
        "_installed_executable",
        lambda _name: executable,
    )
    profile = reviewed_gpt2giga_profile(
        base_url="http://127.0.0.1:8090",
        mode=GatewayMode.MANAGED,
    )

    artifact = resolve_installed_gpt2giga_artifact(profile)

    assert artifact is not None
    assert artifact.verified is True
    assert artifact.artifact_sha256 == GPT2GIGA_WHEEL_SHA256
    assert artifact.source == "locked-registry:pypi/gpt2giga==0.3.0"

    payload.write_text("VALUE = 2\n", encoding="utf-8")
    tampered = resolve_installed_gpt2giga_artifact(profile)
    assert tampered is not None
    assert tampered.verified is False


def test_resolver_rejects_direct_url_install(tmp_path: Path, monkeypatch) -> None:
    payload = tmp_path / "installed.py"
    payload.write_text("VALUE = 1\n", encoding="utf-8")
    executable = tmp_path / "gpt2giga"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        profile_module,
        "distribution",
        lambda _name: _Distribution(payload, direct_url='{"url":"file:///tmp"}'),
    )
    monkeypatch.setattr(
        profile_module,
        "_installed_executable",
        lambda _name: executable,
    )

    artifact = resolve_installed_gpt2giga_artifact(
        reviewed_gpt2giga_profile(
            base_url="http://127.0.0.1:8090",
            mode=GatewayMode.MANAGED,
        )
    )

    assert artifact is not None
    assert artifact.verified is False
