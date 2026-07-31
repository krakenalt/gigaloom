from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.routers.project_catalog import create_router
from gigaloom.ui.services.project_catalog import ProjectCatalogWebService


def _client(tmp_path):
    sessions = InMemoryHarnessSessionStore()
    service = ProjectCatalogWebService.from_data_dir(
        tmp_path / "data",
        session_store=sessions,
    )
    app = FastAPI()
    app.include_router(create_router(service))
    return TestClient(app), sessions


def test_project_catalog_web_crud_detail_and_profile_workspace(tmp_path):
    client, sessions = _client(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    create = client.post(
        "/api/project-catalog",
        json={"path": str(workspace), "display_name": "Demo"},
    )
    assert create.status_code == 200
    project = create.json()
    project_id = project["catalog_project_id"]
    assert project["session_count"] == 0

    sessions.create_session(
        title="Bound",
        metadata={"catalog_project_id": project_id},
    )
    listed = client.get("/api/project-catalog", params={"limit": 10})
    assert listed.status_code == 200
    assert listed.json()["projects"][0]["session_count"] == 1

    profile = client.post(
        f"/api/project-catalog/{project_id}/launch-profiles",
        json={
            "display_name": "Review",
            "agent_hint": "codex",
            "model_hint": "gpt-next",
            "terminal_mode_hint": "direct",
        },
    )
    assert profile.status_code == 200
    profile_id = profile.json()["launch_profile_id"]
    detail = client.get(f"/api/project-catalog/{project_id}")
    assert detail.status_code == 200
    assert detail.json()["launch_profiles"] == [profile.json()]

    renamed = client.patch(
        f"/api/project-catalog/{project_id}",
        json={"display_name": "Renamed", "expected_revision": 1},
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Renamed"
    updated_profile = client.patch(
        f"/api/project-launch-profiles/{profile_id}",
        json={"expected_revision": 1, "model_hint": None},
    )
    assert updated_profile.status_code == 200
    assert updated_profile.json()["model_hint"] is None

    deleted_profile = client.delete(
        f"/api/project-launch-profiles/{profile_id}",
        params={"expected_revision": 2},
    )
    assert deleted_profile.status_code == 200
    removed = client.delete(
        f"/api/project-catalog/{project_id}",
        params={"expected_revision": 2},
    )
    assert removed.status_code == 200
    assert removed.json()["state"] == "tombstoned"
    assert workspace.is_dir()
    assert sessions.get_session(next(iter(sessions.list_sessions())).id)


def test_project_relocation_requires_digest_and_identity_confirmation(tmp_path):
    client, _ = _client(tmp_path)
    original = tmp_path / "old"
    destination = tmp_path / "new"
    original.mkdir()
    destination.mkdir()
    created = client.post(
        "/api/project-catalog",
        json={"path": str(original), "display_name": "Movable"},
    ).json()
    project_id = created["catalog_project_id"]

    preview = client.post(
        f"/api/project-catalog/{project_id}/relocation-preview",
        json={"new_path": str(destination), "expected_revision": 1},
    )
    assert preview.status_code == 200
    assert preview.json()["identity_matches"] is False

    denied = client.post(
        f"/api/project-catalog/{project_id}/relocate",
        json={
            "new_path": str(destination),
            "expected_revision": 1,
            "preview_digest": preview.json()["preview_digest"],
            "confirm_identity_change": False,
        },
    )
    assert denied.status_code == 409
    applied = client.post(
        f"/api/project-catalog/{project_id}/relocate",
        json={
            "new_path": str(destination),
            "expected_revision": 1,
            "preview_digest": preview.json()["preview_digest"],
            "confirm_identity_change": True,
        },
    )
    assert applied.status_code == 200
    assert applied.json()["location"]["canonical_path"] == str(destination.resolve())


def test_project_catalog_web_rejects_stale_mutations(tmp_path):
    client, _ = _client(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    created = client.post(
        "/api/project-catalog",
        json={"path": str(workspace), "display_name": "Demo"},
    ).json()

    stale = client.patch(
        f"/api/project-catalog/{created['catalog_project_id']}",
        json={"display_name": "Stale", "expected_revision": 7},
    )

    assert stale.status_code == 409
    assert "revision" in stale.json()["detail"]
