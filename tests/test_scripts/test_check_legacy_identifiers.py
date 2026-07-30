import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_identifiers.py"
)


def load_checker_module():
    spec = importlib.util.spec_from_file_location(
        "check_legacy_identifiers", SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _roadmap_identifiers() -> tuple[str, ...]:
    return (
        "gpt2giga" + "_harness",
        "packages/gpt2giga-" + "harness",
        "cockpit_" + "v2",
        "cockpit-" + "v2",
        "@gpt2giga/harness-" + "cockpit-" + "v2",
        "gpt2giga-" + "cockpit",
        "GIGALOOM_" + "COCKPIT_OUTPUT",
        "agent_" + "workbench.",
        "gpt2giga-harness-" + "v",
        "gigaloom-" + "v",
    )


def _write_tar(path: Path, member_name: str, payload: bytes) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_policy_contains_the_exact_roadmap_minimum():
    checker = load_checker_module()

    assert checker.DENIED_IDENTIFIERS == _roadmap_identifiers()


def test_entry_scan_detects_path_text_and_python_imports():
    checker = load_checker_module()
    identifier = checker.DENIED_IDENTIFIERS[0]

    violations = checker.scan_entry(
        "source",
        f"src/{identifier}/module.py",
        f"import {identifier}.runtime\n".encode(),
    )

    assert {item.kind for item in violations} == {"path", "text", "ast-import"}
    assert {item.identifier for item in violations} == {identifier}


@pytest.mark.parametrize(
    ("kind", "member_name"),
    [
        ("wheel", "gigaloom/runtime.py"),
        ("sdist", "gigaloom-0.6.0a1/src/gigaloom/runtime.py"),
        ("npm", "package/src/runtime.ts"),
    ],
)
def test_artifact_scan_detects_denied_content(
    tmp_path: Path,
    kind: str,
    member_name: str,
):
    checker = load_checker_module()
    identifier = checker.DENIED_IDENTIFIERS[0]
    suffix = ".whl" if kind == "wheel" else ".tar.gz"
    artifact = tmp_path / f"artifact{suffix}"
    payload = f"legacy={identifier}\n".encode()
    if kind == "wheel":
        with zipfile.ZipFile(artifact, mode="w") as archive:
            archive.writestr(member_name, payload)
    else:
        _write_tar(artifact, member_name, payload)

    violations = checker.check_artifact(artifact, kind)

    assert [(item.scope, item.identifier, item.kind) for item in violations] == [
        (kind, identifier, "text")
    ]


def test_historical_allowlist_is_exact_and_runtime_source_is_not_allowed():
    checker = load_checker_module()
    identifier = checker.DENIED_IDENTIFIERS[0]
    historical = checker.Violation(
        scope="source",
        path="CHANGELOG_en.md",
        identifier=identifier,
        kind="text",
        line=1,
    )
    runtime = checker.Violation(
        scope="source",
        path="src/gigaloom/runtime.py",
        identifier=identifier,
        kind="text",
        line=1,
    )

    assert checker._allowed_source_violation(historical) is True
    assert checker._allowed_source_violation(runtime) is False


def test_current_tracked_source_passes_the_policy():
    checker = load_checker_module()
    repository = Path(__file__).resolve().parents[2]

    assert checker.check_source_tree(repository) == ()
