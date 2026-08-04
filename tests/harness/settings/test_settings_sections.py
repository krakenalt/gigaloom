from __future__ import annotations

import json

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.registry import create_default_registry
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.app import create_app
from gigaloom.ui.services import settings_snapshots


def test_summary_defaults_and_legacy_reads_never_probe_executables(
    tmp_path,
    monkeypatch,
):
    registry = create_default_registry(include_entry_points=False)

    def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("initial Settings reads must not probe an executable")

    for harness in registry.list():
        monkeypatch.setattr(harness, "availability", unexpected_probe)
        for method in (
            "capability_probe",
            "durable_structured_capabilities",
            "executable_resolution",
        ):
            if hasattr(harness, method):
                monkeypatch.setattr(harness, method, unexpected_probe)

    with _client(tmp_path, registry=registry) as client:
        summary = client.get("/api/settings/summary")
        defaults = client.get("/api/settings/defaults")
        legacy = client.get("/api/settings", params={"workspace": str(tmp_path)})

    assert summary.status_code == 200
    assert defaults.status_code == 200
    assert legacy.status_code == 200
    assert all(
        item["status"] == "not_checked"
        for item in defaults.json()["harness_defaults"]["harnesses"]
    )


def test_settings_sections_are_etagged_and_recompose_the_legacy_contract(tmp_path):
    with _client(tmp_path) as client:
        summary = client.get("/api/settings/summary", params={"workspace": tmp_path})
        assert summary.status_code == 200
        assert summary.headers["etag"] == f'"{summary.json()["revision"]}"'
        not_modified = client.get(
            "/api/settings/summary",
            params={"workspace": tmp_path},
            headers={"If-None-Match": summary.headers["etag"]},
        )
        assert not_modified.status_code == 304
        sections = {
            name: client.get(
                f"/api/settings/{name}",
                params={"workspace": tmp_path},
            ).json()
            for name in (
                "runtime",
                "defaults",
                "personalization",
                "workspace",
                "mcp",
                "diagnostics",
            )
        }
        legacy = client.get(
            "/api/settings",
            params={"workspace": tmp_path},
        ).json()

    assert legacy["runtime"] == sections["runtime"]["runtime"]
    assert (
        summary.json()["workspace_id"]
        == sections["workspace"]["workspace"]["project_id"]
    )
    assert legacy["routes"] == sections["defaults"]["routes"]
    assert legacy["harness_defaults"] == sections["defaults"]["harness_defaults"]
    assert legacy["workspace"] == sections["workspace"]["workspace"]
    assert legacy["diagnostics"]["content_free"] is True
    assert legacy["mcp"] == {
        key: value
        for key, value in sections["mcp"]["mcp"].items()
        if key != "truncated"
    }


def test_summary_revision_tracks_defaults_project_and_provider_sources(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _client(tmp_path) as client:
        initial = client.get(
            "/api/settings/summary",
            params={"workspace": workspace},
        ).json()
        saved = client.patch(
            "/api/settings/defaults",
            json={
                "expected_revision": client.get("/api/settings/defaults").json()[
                    "settings_revision"
                ],
                "defaults": {"workspace_policy": "current"},
            },
        )
        assert saved.status_code == 200
        after_defaults = client.get(
            "/api/settings/summary",
            params={"workspace": workspace},
        ).json()
        personalization = client.patch(
            "/api/settings/personalization",
            json={
                "expected_revision": after_defaults["sections"]["personalization"][
                    "revision"
                ],
                "developer_instructions": "Keep updates concise.",
            },
        )
        assert personalization.status_code == 200
        after_personalization = client.get(
            "/api/settings/summary",
            params={"workspace": workspace},
        ).json()

        config_dir = workspace / ".giga"
        config_dir.mkdir()
        (config_dir / "harness.toml").write_text(
            '[project]\nname = "Settings fixture"\n',
            encoding="utf-8",
        )
        after_project = client.get(
            "/api/settings/summary",
            params={"workspace": workspace},
        ).json()

        provider = client.post(
            "/api/providers",
            json={
                "id": "settings-fixture",
                "display_name": "Settings fixture",
                "protocol": "openai_compatible",
                "dialect": "openai-responses-v1",
                "base_url": "https://fixture.invalid",
                "authentication": {"ownership": "none"},
                "enabled": True,
                "offline": True,
            },
        )
        assert provider.status_code == 200
        after_provider = client.get(
            "/api/settings/summary",
            params={"workspace": workspace},
        ).json()

    assert initial["revision"] != after_defaults["revision"]
    assert (
        initial["sections"]["defaults"]["revision"]
        != (after_defaults["sections"]["defaults"]["revision"])
    )
    assert (
        after_defaults["sections"]["personalization"]["revision"]
        != after_personalization["sections"]["personalization"]["revision"]
    )
    assert (
        after_personalization["sections"]["workspace"]["revision"]
        != (after_project["sections"]["workspace"]["revision"])
    )
    assert (
        after_defaults["sections"]["mcp"]["revision"]
        != (after_project["sections"]["mcp"]["revision"])
    )
    assert (
        after_project["providers"]["revision"]
        != (after_provider["providers"]["revision"])
    )


def test_mcp_history_tail_and_snapshot_cache_are_hard_bounded(tmp_path):
    history_path = tmp_path / "history.jsonl"
    filler = {"server_id": "ignored", "status": "ready", "padding": "x" * 512}
    with history_path.open("w", encoding="utf-8") as stream:
        for _ in range(2_000):
            stream.write(json.dumps(filler, sort_keys=True) + "\n")
        stream.write(json.dumps({"server_id": "selected", "status": "healthy"}))
        stream.write("\n")

    health = settings_snapshots._bounded_mcp_health(history_path, {"selected"})
    assert health == {"selected": "healthy"}
    assert (
        history_path.stat().st_size > settings_snapshots.MAX_SETTINGS_MCP_HISTORY_BYTES
    )

    service = settings_snapshots.SettingsSnapshotService(
        config=HarnessConfig(data_dir=str(tmp_path / "state")),
        settings_store=create_app(
            HarnessConfig(data_dir=str(tmp_path / "other-state")),
            registry=create_default_registry(include_entry_points=False),
            store=InMemoryHarnessSessionStore(),
        ).state.harness_settings_store,
        provider_settings_service=_StaticProviders(),
        registry=create_default_registry(include_entry_points=False),
        async_diagnostics=_StaticDiagnostics(),
        cache_entries=2,
    )
    service.runtime()
    service.defaults()
    service.summary(str(tmp_path))
    assert service.cache_size() == 2


class _StaticProviders:
    def list(self):
        return {"providers": []}


class _StaticDiagnostics:
    def snapshot(self):
        return {}


def _client(tmp_path, *, registry=None) -> TestClient:
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    return TestClient(
        create_app(
            config,
            registry=registry or create_default_registry(include_entry_points=False),
            store=InMemoryHarnessSessionStore(),
        )
    )
