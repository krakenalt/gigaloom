from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "release.py"


def _module():
    specification = importlib.util.spec_from_file_location(
        "gigaloom_release_tool", SCRIPT_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _repository(tmp_path: Path, module) -> Path:
    root = tmp_path / "repository"
    _write(root / "release/version.toml", 'version = "0.7.0"\n')
    _write(
        root / "pyproject.toml",
        '[project]\nname = "gigaloom"\nversion = "0.7.0"\n',
    )
    _write(
        root / "uv.lock",
        'version = 1\n\n[[package]]\nname = "gigaloom"\nversion = "0.7.0"\n'
        'source = { editable = "." }\n',
    )
    _write(
        root / "web/package.json",
        json.dumps({"name": "@gigaloom/web", "version": "0.7.0"}, indent=2) + "\n",
    )
    _write(
        root / "web/package-lock.json",
        json.dumps(
            {
                "name": "@gigaloom/web",
                "version": "0.7.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "@gigaloom/web", "version": "0.7.0"}},
            },
            indent=2,
        )
        + "\n",
    )
    _write(root / "release/release.json", "{}\n")
    _write(
        root / "release/external-evidence.json",
        '{"schema_version":"evidence-v1","release":"0.7.0"}\n',
    )
    _write(root / "release/artifact-set.toml", "schema_version = 1\n")
    _write(
        root / "release/candidate-report.md",
        "# GigaLoom release candidate report\n\n"
        f"{module.GENERATED_DOC_MARKER}\n"
        "<!-- release.py:identity:start -->\n"
        "Candidate identity: `0.7.0` / `gigaloom==0.7.0` /\n"
        "`@gigaloom/web@0.7.0`.\n"
        "<!-- release.py:identity:end -->\n",
    )
    for relative in module.DOCUMENT_PROJECTIONS:
        _write(
            root / relative,
            f"# Projection\n\n{module.GENERATED_DOC_MARKER}\n\n"
            "`gigaloom==0.7.0` and `@gigaloom/web@0.7.0`\n",
        )
    _write(
        root / "web/scripts/package-contract.mjs",
        f'{module.GENERATED_SCRIPT_MARKER}\nconst expectedVersion = "0.7.0";\n',
    )
    _write(
        root / "web/scripts/package-contract.unit.mjs",
        f"{module.GENERATED_SCRIPT_MARKER}\n"
        'const expected = {\n  version: "0.7.0",\n};\n',
    )
    for relative in module.CHANGELOG_PROJECTIONS:
        _write(
            root / relative,
            "# Changelog\n\n## [0.7.0] - 2026-07-31\n\n"
            "- Existing release.\n\n---\n\n"
            "[0.7.0]: https://example.test/v0.7.0\n",
        )
    for relative, content in module.build_projections(
        root, module.release_identity("0.7.0")
    ).items():
        _write(root / relative, content)
    return root


def test_current_stable_release_projections_verify() -> None:
    module = _module()

    assert module.verify_projections(REPOSITORY_ROOT) == ()


def test_prepare_is_complete_idempotent_and_detects_drift(tmp_path: Path) -> None:
    module = _module()
    root = _repository(tmp_path, module)

    changed = module.apply_projections(
        root,
        module.build_projections(root, module.release_identity("0.8.0-alpha.1")),
    )
    assert module.VERSION_PATH in changed
    assert module.verify_projections(root) == ()
    assert 'version = "0.8.0-alpha.1"' in (root / module.VERSION_PATH).read_text()
    assert 'version = "0.8.0a1"' in (root / "pyproject.toml").read_text()
    assert 'name = "gigaloom"\nversion = "0.8.0a1"' in (root / "uv.lock").read_text()
    assert (
        'const expectedVersion = "0.8.0-alpha.1";'
        in (root / "web/scripts/package-contract.mjs").read_text()
    )
    assert "## [0.8.0-alpha.1] - Unreleased" in (root / "CHANGELOG_en.md").read_text()

    before = {
        relative: ((root / relative).read_bytes(), (root / relative).stat().st_mtime_ns)
        for relative in module.build_projections(
            root, module.read_canonical_identity(root)
        )
    }
    assert (
        module.apply_projections(
            root,
            module.build_projections(root, module.read_canonical_identity(root)),
        )
        == ()
    )
    after = {
        relative: ((root / relative).read_bytes(), (root / relative).stat().st_mtime_ns)
        for relative in before
    }
    assert after == before

    package = root / "web/package.json"
    package.write_text(package.read_text().replace("0.8.0-alpha.1", "0.8.0"))
    assert module.verify_projections(root) == (Path("web/package.json"),)


def test_runtime_package_contract_drift_is_detected(tmp_path: Path) -> None:
    module = _module()
    root = _repository(tmp_path, module)
    module.apply_projections(
        root,
        module.build_projections(root, module.release_identity("0.8.0-alpha.1")),
    )
    contract = root / "web/scripts/package-contract.mjs"
    contract.write_text(
        contract.read_text().replace("0.8.0-alpha.1", "0.8.0"),
        encoding="utf-8",
    )

    assert module.verify_projections(root) == (
        Path("web/scripts/package-contract.mjs"),
    )


def test_diff_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    root = _repository(tmp_path, module)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    assert module.main(["--root", str(root), "diff", "0.8.0-alpha.1"]) == 0

    assert "release/version.toml" in capsys.readouterr().out
    assert {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before


def test_prepare_and_verify_cli_are_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    root = _repository(tmp_path, module)

    assert module.main(["--root", str(root), "prepare", "0.8.0-alpha.1"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"]
    assert module.main(["--root", str(root), "verify"]) == 0
    assert "verified" in capsys.readouterr().out
    assert module.main(["--root", str(root), "prepare", "0.8.0-alpha.1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"changed": []}

    manifest = root / "release/release.json"
    manifest.write_text(manifest.read_text().replace("0.8.0-alpha.1", "0.8.0"))
    with pytest.raises(SystemExit) as error:
        module.main(["--root", str(root), "verify"])
    assert error.value.code == 2
    assert "release/release.json" in capsys.readouterr().err


def test_artifact_set_stages_role_named_files(tmp_path: Path) -> None:
    module = _module()
    root = _repository(tmp_path, module)
    sources = {
        "src/gigaloom/ui/web/assets/_build/licenses.json": b"{}\n",
        "src/gigaloom/ui/web/assets/_build/provenance.json": b"{}\n",
        "src/gigaloom/ui/web/assets/_build/sbom.cdx.json": b"{}\n",
        "tests/fixtures/run_capsules/read_only_run.json": b"{}\n",
    }
    for relative, content in sources.items():
        _write(root / relative, content)
    output = root / "dist/release-candidate"

    copied = module.stage_artifacts(root, output)

    assert {path.name for path in copied} == {
        "artifact-set.toml",
        "candidate-report.md",
        "external-evidence.json",
        "licenses.json",
        "release.json",
        "run-capsule-fixture.json",
        "sbom.cdx.json",
        "web-provenance.json",
    }
    assert {path.name for path in output.iterdir()} == {
        "artifact-set.toml",
        "candidate-report.md",
        "external-evidence.json",
        "licenses.json",
        "release.json",
        "run-capsule-fixture.json",
        "sbom.cdx.json",
        "web-provenance.json",
    }


@pytest.mark.parametrize("version", ["0.8", "v0.8.0", "0.8.0-alpha.0", "latest"])
def test_release_version_validation_is_fail_closed(version: str) -> None:
    module = _module()

    with pytest.raises(module.ReleasePreparationError, match="unsupported"):
        module.release_identity(version)
