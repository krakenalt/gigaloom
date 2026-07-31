from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import time

import pytest

from gigaloom.projects import api
from gigaloom.projects.api import (
    PythonImpactIndexCache,
    StalePythonImpactIndexError,
)


def test_python_impact_reports_imports_symbols_tests_contracts_and_owners(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(
        repo / "src/example/service.py",
        '__all__ = ["public_api"]\n\ndef public_api() -> str:\n    return "ok"\n',
    )
    _write(
        repo / "src/example/consumer.py",
        "from example.service import public_api\n\nRESULT = public_api()\n",
    )
    _write(
        repo / "tests/test_service.py",
        "from example.service import public_api\n\n\ndef test_api():\n    assert public_api()\n",
    )
    _write(
        repo / "src/example/plugins.py",
        "import importlib\n\n"
        'PLUGIN = importlib.import_module("example.service")\n'
        'FACTORY = getattr(PLUGIN, "factory", None)\n',
    )
    _write(
        repo / ".github/CODEOWNERS",
        "/src/example/ @platform\n/tests/ @quality\n",
    )
    _commit_all(repo)

    index = api.compile_python_impact_index(repo)
    result = api.project_python_impact(index, ("src/example/service.py",))

    assert [item.relative_path for item in result.affected_files] == [
        "src/example/consumer.py",
        "tests/test_service.py",
    ]
    assert result.affected_files[0].reasons == (
        "import:example.service",
        "symbol_reference:example.service.public_api",
    )
    assert result.affected_files[0].owners == ("@platform",)
    assert result.affected_files[1].owners == ("@quality",)
    assert result.changed_files[0].owners == ("@platform",)
    assert result.changed_files[0].reasons == ("changed",)
    assert result.nearest_tests == ("tests/test_service.py",)
    assert result.package_boundaries == ("example",)
    assert result.public_contracts[0].markers == ("declares___all__",)
    assert "public_api" in result.public_contracts[0].public_symbols
    assert {(item.kind.value, item.relative_path) for item in result.uncertainties} == {
        ("dynamic_import", "src/example/plugins.py"),
        ("runtime_wiring", "src/example/plugins.py"),
    }
    assert result.advisory_only is True
    assert result.to_dict()["advisory_only"] is True


def test_deleted_or_unavailable_change_remains_explicitly_uncertain(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(
        repo / "src/example/consumer.py",
        "from example.removed import public_api\n\nRESULT = public_api()\n",
    )
    _commit_all(repo)

    result = api.analyze_python_impact(repo, ("src/example/removed.py",))

    assert result.affected_files[0].relative_path == "src/example/consumer.py"
    assert {(item.kind.value, item.relative_path) for item in result.uncertainties} == {
        ("source_unavailable", "src/example/removed.py")
    }


def test_scan_is_bounded_and_rejects_paths_outside_the_project(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "src/example/one.py", "VALUE = 1\n")
    _write(repo / "src/example/two.py", "VALUE = 2\n")
    _commit_all(repo)

    index = api.compile_python_impact_index(repo, max_files=1)

    assert index.scanned_file_count == 1
    assert index.uncertainties[0].kind.value == "scan_truncated"
    with pytest.raises(ValueError, match="relative Python"):
        api.project_python_impact(index, ("../outside.py",))


def test_same_git_visible_sources_compile_to_the_same_index_digest(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "src/example/service.py", "VALUE = 1\n")
    _commit_all(repo)

    first = api.compile_python_impact_index(repo)
    same = api.compile_python_impact_index(repo)
    _write(repo / "src/example/service.py", "VALUE = 2\n")
    changed = api.compile_python_impact_index(repo)

    assert first.index_digest == same.index_digest
    assert first.source_revision == changed.source_revision
    assert first.index_digest != changed.index_digest


def test_impact_cache_is_bounded_and_exact_snapshot_bound(tmp_path: Path) -> None:
    first_repo = _repository(tmp_path / "first")
    second_repo = _repository(tmp_path / "second")
    _write(first_repo / "src/example/service.py", "VALUE = 1\n")
    _write(second_repo / "src/example/service.py", "VALUE = 2\n")
    _commit_all(first_repo)
    _commit_all(second_repo)
    first = api.compile_python_impact_index(first_repo)
    second = api.compile_python_impact_index(second_repo)
    cache = PythonImpactIndexCache(max_entries=1)

    cache.put(first_repo, first)
    assert (
        cache.get(
            first_repo,
            index_digest=first.index_digest,
            source_revision=first.source_revision,
        )
        is first
    )
    cache.put(second_repo, second)

    with pytest.raises(StalePythonImpactIndexError, match="resnapshot"):
        cache.get(
            first_repo,
            index_digest=first.index_digest,
            source_revision=first.source_revision,
        )
    with pytest.raises(StalePythonImpactIndexError, match="source revision"):
        cache.get(
            second_repo,
            index_digest=second.index_digest,
            source_revision="stale-revision",
        )


def test_5k_file_cold_compile_and_warm_projection_budgets(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    for index in range(4_999):
        _write(repo / f"src/example/module_{index:04d}.py", "VALUE = 1\n")
    _commit_all(repo)

    cold_started = time.perf_counter()
    impact_index = api.compile_python_impact_index(repo)
    cold_seconds = time.perf_counter() - cold_started
    cache = PythonImpactIndexCache(max_entries=1)
    cache.put(repo, impact_index)
    warm_started = time.perf_counter()
    retained = cache.get(
        repo,
        index_digest=impact_index.index_digest,
        source_revision=impact_index.source_revision,
    )
    result = api.project_python_impact(
        retained,
        ("src/example/module_0000.py",),
    )
    warm_seconds = time.perf_counter() - warm_started

    assert impact_index.scanned_file_count == 5_000
    assert result.scanned_file_count == 5_000
    assert cold_seconds <= 3.5
    assert warm_seconds <= 0.3


def _repository(tmp_path: Path) -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run((git, "init", "-q", "-b", "main"), cwd=repo, check=True)
    _write(repo / "src/example/__init__.py", "")
    return repo


def _commit_all(repo: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run((git, "add", "."), cwd=repo, check=True)
    subprocess.run(
        (
            git,
            "-c",
            "user.name=Impact Fixture",
            "-c",
            "user.email=impact@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ),
        cwd=repo,
        check=True,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
