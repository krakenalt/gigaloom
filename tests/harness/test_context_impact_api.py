from __future__ import annotations

from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.contracts import (
    ContextDisposition,
    ContextEntryKind,
    ContextFreshness,
    ContextSourceDescriptor,
    InclusionReason,
)
from gigaloom.execution.api import (
    NativeCodexContextMode,
    NativeCodexContextProjection,
    compile_native_codex_context,
)
from gigaloom.ui.app import create_app


SHA_A = "a" * 64


class _ContextOwner:
    def __init__(self, projection: NativeCodexContextProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[str, str, str]] = []

    def get_native_codex_context(
        self,
        *,
        session_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> NativeCodexContextProjection:
        self.calls.append((session_id, owner_id, workspace_id))
        return self.projection


def _context(session_id: str = "session_1") -> NativeCodexContextProjection:
    return compile_native_codex_context(
        harness_session_id=session_id,
        native_thread_id="thread-1",
        mode=NativeCodexContextMode.STRUCTURED,
        source_revision="1" * 40,
        config_digest=SHA_A,
        sources=(
            ContextSourceDescriptor(
                source_id="instruction.repository",
                kind=ContextEntryKind.INSTRUCTION,
                source_digest=SHA_A,
                disposition=ContextDisposition.INCLUDE,
                freshness=ContextFreshness.CURRENT,
                inclusion_reason=InclusionReason.MANDATORY_INSTRUCTION,
                relative_path="AGENTS.md",
                protected=True,
            ),
        ),
    )


def _client(tmp_path: Path, owner: _ContextOwner | None = None) -> TestClient:
    return TestClient(
        create_app(
            HarnessConfig(data_dir=str(tmp_path / "data")),
            context_projection_query=owner,
        )
    )


def test_context_api_returns_owner_scoped_content_free_projection(
    tmp_path: Path,
) -> None:
    owner = _ContextOwner(_context())
    response = _client(tmp_path, owner).get(
        "/api/operator/sessions/session_1/context",
        params={"workspace_id": "workspace-1"},
    )

    assert response.status_code == 200
    assert response.json()["context"] == owner.projection.to_dict()
    assert owner.calls == [("session_1", "local_operator", "workspace-1")]
    serialized = response.text
    assert "thread-1" not in serialized
    assert "not_observable" in serialized


def test_context_api_fails_closed_on_missing_owner_and_binding_mismatch(
    tmp_path: Path,
) -> None:
    unavailable = _client(tmp_path).get(
        "/api/operator/sessions/session_1/context",
        params={"workspace_id": "workspace-1"},
    )
    mismatched = _client(tmp_path, _ContextOwner(_context("session_2"))).get(
        "/api/operator/sessions/session_1/context",
        params={"workspace_id": "workspace-1"},
    )

    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "context_unavailable"
    assert mismatched.status_code == 403
    assert mismatched.json()["detail"]["code"] == "context_binding_mismatch"


def test_impact_api_projects_advisory_python_relationships(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo / "src/example/service.py", "def public_api():\n    return 1\n")
    _write(
        repo / "src/example/consumer.py",
        "from example.service import public_api\nRESULT = public_api()\n",
    )
    _write(
        repo / "tests/test_service.py",
        "from example.service import public_api\n\ndef test_api():\n    assert public_api()\n",
    )
    _commit(repo)

    response = _client(tmp_path).get(
        "/api/project/impact",
        params={
            "workspace": str(repo),
            "changed_path": "src/example/service.py",
        },
    )

    assert response.status_code == 200
    body = response.json()
    impact = body["impact"]
    assert body["cache_hit"] is False
    assert impact["advisory_only"] is True
    assert impact["changed_paths"] == ["src/example/service.py"]
    assert [item["relative_path"] for item in impact["affected_files"]] == [
        "src/example/consumer.py",
        "tests/test_service.py",
    ]
    assert impact["nearest_tests"] == ["tests/test_service.py"]

    warm = _client(tmp_path).get(
        "/api/project/impact",
        params={
            "workspace": str(repo),
            "changed_path": "src/example/service.py",
            "index_digest": impact["index_digest"],
            "source_revision": impact["source_revision"],
        },
    )
    assert warm.status_code == 409

    client = _client(tmp_path)
    cold = client.get(
        "/api/project/impact",
        params={
            "workspace": str(repo),
            "changed_path": "src/example/service.py",
        },
    )
    cold_impact = cold.json()["impact"]
    warm = client.get(
        "/api/project/impact",
        params={
            "workspace": str(repo),
            "changed_path": "src/example/service.py",
            "index_digest": cold_impact["index_digest"],
            "source_revision": cold_impact["source_revision"],
        },
    )
    assert warm.status_code == 200
    assert warm.json()["cache_hit"] is True
    assert warm.json()["impact"] == cold_impact


def test_impact_api_rejects_unbounded_or_escaping_requests(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo / "src/example/service.py", "VALUE = 1\n")
    _commit(repo)
    client = _client(tmp_path)

    escaping = client.get(
        "/api/project/impact",
        params={"workspace": str(repo), "changed_path": "../outside.py"},
    )
    unbounded = client.get(
        "/api/project/impact",
        params=[
            ("workspace", str(repo)),
            *(("changed_path", f"src/example/{index}.py") for index in range(257)),
        ],
    )

    assert escaping.status_code == 422
    assert escaping.json()["detail"]["code"] == "invalid_impact_request"
    assert unbounded.status_code == 422

    partial_binding = client.get(
        "/api/project/impact",
        params={
            "workspace": str(repo),
            "changed_path": "src/example/service.py",
            "index_digest": "a" * 64,
        },
    )
    assert partial_binding.status_code == 422


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", "-b", "main"), cwd=repo, check=True)
    _write(repo / "src/example/__init__.py", "")
    return repo


def _commit(repo: Path) -> None:
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Context API Fixture",
            "-c",
            "user.email=context@example.invalid",
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
