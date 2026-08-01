"""Hermetic isolated npx and uvx resolution/installation coverage."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from gigaloom.harnesses.agent_profiles.installations import (
    AgentInstallError,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
    NpxAgentInstaller,
    NpxPackageResolver,
    UvxAgentInstaller,
    UvxPackageResolver,
)
from gigaloom.harnesses.agent_profiles.installations.commands import (
    PackageCommandResult,
)
from gigaloom.harnesses.agent_profiles.registry import decode_registry_document


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
NPM_INTEGRITY = "sha512-YWJjZA=="
LOCK_HASH = "a" * 64


class FakePackageRunner:
    """Filesystem-producing fake for exact package-manager argv contracts."""

    def __init__(self, *, tamper_npm_ci: bool = False) -> None:
        self.commands = []
        self.tamper_npm_ci = tamper_npm_ci

    def run(self, command):  # noqa: ANN001, ANN201
        self.commands.append(command)
        argv = command.argv
        if argv[1:3] == ("install", "--package-lock-only"):
            self._npm_lock(command)
        elif argv[1] == "ci":
            self._npm_ci(command)
        elif argv[1:3] == ("pip", "compile"):
            self._uv_compile(command)
        elif argv[1] == "venv":
            self._uv_venv(command)
        elif argv[1:3] == ("pip", "sync"):
            self._uv_sync(command)
        else:
            raise AssertionError(f"unexpected fake command: {argv}")
        return PackageCommandResult(return_code=0)

    def _npm_lock(self, command):  # noqa: ANN001
        prefix = Path(command.argv[command.argv.index("--prefix") + 1])
        exact = command.argv[-1]
        package_name, version = _split_npx(exact)
        self._write_npm_lock(prefix, package_name, version, NPM_INTEGRITY)

    def _npm_ci(self, command):  # noqa: ANN001
        prefix = Path(command.argv[command.argv.index("--prefix") + 1])
        package_json = json.loads((prefix / "package.json").read_text(encoding="utf-8"))
        package_name, version = next(iter(package_json["dependencies"].items()))
        if self.tamper_npm_ci:
            self._write_npm_lock(
                prefix,
                package_name,
                version,
                "sha512-ZGVmZw==",
            )
        package_root = prefix / "node_modules" / package_name
        package_root.mkdir(parents=True)
        entrypoint = package_name.rsplit("/", maxsplit=1)[-1]
        (package_root / "package.json").write_text(
            json.dumps(
                {
                    "name": package_name,
                    "version": version,
                    "bin": {entrypoint: "bin/agent.js"},
                }
            ),
            encoding="utf-8",
        )
        bin_root = prefix / "node_modules/.bin"
        bin_root.mkdir(parents=True)
        (bin_root / entrypoint).write_text("#!/usr/bin/env node\n", encoding="utf-8")

    @staticmethod
    def _write_npm_lock(prefix, package_name, version, integrity):  # noqa: ANN001
        lock = {
            "name": "gigaloom-managed-acp-agent",
            "version": "0.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "gigaloom-managed-acp-agent",
                    "version": "0.0.0",
                    "dependencies": {package_name: version},
                },
                f"node_modules/{package_name}": {
                    "version": version,
                    "resolved": f"https://registry.npmjs.org/{package_name}/-/{version}.tgz",
                    "integrity": integrity,
                },
            },
        }
        (prefix / "package-lock.json").write_text(
            json.dumps(lock),
            encoding="utf-8",
        )

    @staticmethod
    def _uv_compile(command):  # noqa: ANN001
        output = Path(command.argv[command.argv.index("--output-file") + 1])
        source = Path(command.argv[-1]).read_text(encoding="utf-8").strip()
        package_name, version = source.split("==", maxsplit=1)
        output.write_text(
            f"{package_name}=={version} \\\n    --hash=sha256:{LOCK_HASH}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _uv_venv(command):  # noqa: ANN001
        root = Path(command.argv[-1])
        bin_root = root / "bin"
        bin_root.mkdir(parents=True)
        (bin_root / "python").write_bytes(b"private-python")

    @staticmethod
    def _uv_sync(command):  # noqa: ANN001
        python = Path(command.argv[command.argv.index("--python") + 1])
        (python.parent / "generic-uvx").write_text(
            "#!/usr/bin/env python\n",
            encoding="utf-8",
        )


def _catalog():
    payload = json.dumps(
        {
            "version": "1.0.0",
            "agents": [
                {
                    "id": "generic-npx",
                    "name": "Generic npm agent",
                    "version": "1.2.3",
                    "description": "Hermetic npm fixture",
                    "distribution": {"npx": {"package": "@example/generic-npx@1.2.3"}},
                },
                {
                    "id": "generic-uvx",
                    "name": "Generic Python agent",
                    "version": "2.3.4",
                    "description": "Hermetic uv fixture",
                    "distribution": {"uvx": {"package": "generic-uvx==2.3.4"}},
                },
            ],
        },
        separators=(",", ":"),
    ).encode()
    return decode_registry_document(payload, fetched_at=NOW)


def _entry(registry_id: str):
    return next(item for item in _catalog().entries if item.registry_id == registry_id)


def _executable(tmp_path: Path, name: str, payload: bytes = b"tool") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    path.chmod(0o700)
    return path


def _plan(tmp_path: Path, entry, snapshot, evidence):  # noqa: ANN001
    result = AgentInstallPlanner(
        AgentInstallPlannerPolicy(
            platform="darwin",
            architecture="aarch64",
            data_root=str(tmp_path),
        )
    ).plan(entry, snapshot, resolutions=(evidence,), now=NOW)
    assert result.plan is not None
    return result.plan


def test_npx_resolves_integrity_then_installs_with_no_scripts_in_private_prefix(
    tmp_path,
):
    catalog = _catalog()
    entry = _entry("generic-npx")
    npm = _executable(tmp_path, "npm")
    runner = FakePackageRunner()
    resolution = NpxPackageResolver(tmp_path, npm, runner).resolve(
        entry.distributions[0]
    )
    plan = _plan(tmp_path, entry, catalog.snapshot, resolution.evidence)

    result = NpxAgentInstaller(
        tmp_path,
        npm,
        runner,
        clock=lambda: NOW,
    ).install(plan, resolution, confirmed=True)

    assert result.package_integrity == NPM_INTEGRITY
    assert result.artifact.lock_digest == resolution.evidence.lock_digest
    assert result.artifact.executable_relative_path == "node_modules/.bin/generic-npx"
    assert Path(result.artifact.managed_root).is_dir()
    npm_commands = [
        command for command in runner.commands if Path(command.argv[0]) == npm
    ]
    assert all(
        "--global" not in command.argv and "-g" not in command.argv
        for command in npm_commands
    )
    assert all("--ignore-scripts" in command.argv for command in npm_commands)
    assert all(Path(command.cwd).is_relative_to(tmp_path) for command in npm_commands)


def test_npx_integrity_change_after_resolution_fails_without_publication(tmp_path):
    catalog = _catalog()
    entry = _entry("generic-npx")
    npm = _executable(tmp_path, "npm")
    resolver_runner = FakePackageRunner()
    resolution = NpxPackageResolver(tmp_path, npm, resolver_runner).resolve(
        entry.distributions[0]
    )
    plan = _plan(tmp_path, entry, catalog.snapshot, resolution.evidence)

    with pytest.raises(AgentInstallError, match="npm_integrity_mismatch"):
        NpxAgentInstaller(
            tmp_path,
            npm,
            FakePackageRunner(tamper_npm_ci=True),
            clock=lambda: NOW,
        ).install(plan, resolution, confirmed=True)

    assert not Path(plan.managed_root).exists()
    assert not Path(plan.staging_root).exists()


def test_uvx_compiles_hashed_lock_and_syncs_private_interpreter_environment(tmp_path):
    catalog = _catalog()
    entry = _entry("generic-uvx")
    uv = _executable(tmp_path, "uv")
    interpreter = _executable(tmp_path, "python", b"python-interpreter")
    runner = FakePackageRunner()
    resolution = UvxPackageResolver(tmp_path, uv, runner).resolve(
        entry.distributions[0],
        interpreter=interpreter,
    )
    plan = _plan(tmp_path, entry, catalog.snapshot, resolution.evidence)

    result = UvxAgentInstaller(
        tmp_path,
        uv,
        runner,
        clock=lambda: NOW,
    ).install(plan, resolution, confirmed=True)

    assert result.package_integrity is None
    assert result.artifact.lock_digest == resolution.evidence.lock_digest
    assert result.artifact.executable_relative_path == "environment/bin/generic-uvx"
    assert (Path(result.artifact.managed_root) / ".uv-resolution.json").is_file()
    uv_commands = [
        command for command in runner.commands if Path(command.argv[0]) == uv
    ]
    assert any("--generate-hashes" in command.argv for command in uv_commands)
    assert any("--require-hashes" in command.argv for command in uv_commands)
    assert all("--no-config" in command.argv for command in uv_commands)
    assert all(
        "--no-sources" in command.argv
        for command in uv_commands
        if "pip" in command.argv
    )
    assert all(Path(command.cwd).is_relative_to(tmp_path) for command in uv_commands)
    assert all(
        dict(command.environment).get("UV_PYTHON_DOWNLOADS") == "never"
        for command in uv_commands
    )


def test_uvx_rejects_lock_without_hashes_or_exact_target(tmp_path):
    entry = _entry("generic-uvx")
    uv = _executable(tmp_path, "uv")
    interpreter = _executable(tmp_path, "python", b"python-interpreter")

    class InvalidLockRunner(FakePackageRunner):
        @staticmethod
        def _uv_compile(command):  # noqa: ANN001
            output = Path(command.argv[command.argv.index("--output-file") + 1])
            output.write_text("generic-uvx>=2\n", encoding="utf-8")

    with pytest.raises(AgentInstallError, match="not_exact_or_hashed"):
        UvxPackageResolver(tmp_path, uv, InvalidLockRunner()).resolve(
            entry.distributions[0],
            interpreter=interpreter,
        )


def _split_npx(value: str) -> tuple[str, str]:
    if value.startswith("@"):
        position = value.rfind("@")
    else:
        position = value.find("@")
    return value[:position], value[position + 1 :]
