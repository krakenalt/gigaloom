from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "audit_repository_literals.py"


def _load_auditor():
    spec = importlib.util.spec_from_file_location(
        "audit_repository_literals", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = _load_auditor()


def _policy():
    return auditor.load_policy(REPOSITORY_ROOT / "release" / "literal-policy.toml")


def test_policy_detects_task_labels_in_text_and_paths(tmp_path: Path) -> None:
    policy = _policy()
    task_label = "P" + "0-02"

    result = auditor.audit_entries(
        tmp_path,
        policy,
        (("tests/test_feature_i1.py", f"revision={task_label}\n".encode()),),
    )

    assert {violation.kind for violation in result.violations} == {
        "implementation label in path",
        "implementation label in text",
        "missing canonical release source",
    }


def test_historical_archive_is_an_explicit_label_exception(tmp_path: Path) -> None:
    policy = _policy()
    release = tmp_path / "release"
    release.mkdir()
    (release / "release.json").write_text('{"release":"0.7.0"}\n')
    task_label = "G" + "4-00"

    result = auditor.audit_entries(
        tmp_path,
        policy,
        (("docs/archive/decision.md", f"historical {task_label}\n".encode()),),
    )

    assert result.violations == ()


def test_policy_detects_unqualified_task_labels(tmp_path: Path) -> None:
    policy = _policy()
    task_label = "N" + "4"

    result = auditor.audit_entries(
        tmp_path,
        policy,
        (("src/gigaloom/catalog.py", f"owner={task_label}\n".encode()),),
    )

    assert any(violation.rule == "bare_task" for violation in result.violations)


def test_release_literals_require_a_declared_semantic_category(tmp_path: Path) -> None:
    policy = _policy()
    release = tmp_path / "release"
    release.mkdir()
    (release / "release.json").write_text('{"release":"0.7.0"}\n')

    result = auditor.audit_entries(
        tmp_path,
        policy,
        (("src/gigaloom/unclassified.py", b'VERSION = "0.7.0"\n'),),
    )

    assert [violation.kind for violation in result.violations] == [
        "unclassified release literal"
    ]


def test_current_repository_passes_strict_literal_policy() -> None:
    result = auditor.audit_repository(REPOSITORY_ROOT, _policy())

    assert result.violations == ()
