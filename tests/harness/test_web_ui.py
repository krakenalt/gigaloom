from __future__ import annotations

import gzip
import json

from fastapi.testclient import TestClient
import pytest

from gigaloom.config import HarnessConfig
from gigaloom.registry import create_default_registry
from gigaloom.ui.app import create_app
from gigaloom.ui.web import (
    CockpitV2AssetNotFoundError,
    load_web_asset,
    load_web_manifest,
    load_web_shell,
)


def _client(data_dir) -> TestClient:
    return TestClient(
        create_app(
            HarnessConfig(data_dir=str(data_dir)),
            registry=create_default_registry(include_entry_points=False),
        )
    )


def test_web_manifest_binds_hashed_split_identity_assets():
    manifest = load_web_manifest()

    assert manifest.entry == "index.html"
    assert set(manifest.initial).issubset(manifest.assets)
    assert any(name.startswith("assets/workbench-") for name in manifest.assets)
    assert any(name.startswith("assets/runs-") for name in manifest.assets)
    assert any(name.startswith("assets/markdown-") for name in manifest.assets)
    assert any(name.startswith("assets/raw-evidence-") for name in manifest.assets)
    assert manifest.assets["brand/gigaloom-mark.svg"].media_type == "image/svg+xml"
    assert (
        manifest.assets["brand/gigaloom.webmanifest"].media_type
        == "application/manifest+json"
    )
    assert all(
        asset.gzip_name is None and asset.brotli_name is None
        for asset in manifest.assets.values()
    )
    assert "/web/assets/" in load_web_shell()


def test_web_loader_rejects_unknown_and_direct_compressed_paths():
    manifest = load_web_manifest()
    javascript = next(name for name in manifest.assets if name.endswith(".js"))
    compressed, asset = load_web_asset(javascript, encoding="gzip")
    identity, _ = load_web_asset(javascript)

    assert gzip.decompress(compressed) == identity
    assert asset.name == javascript
    with pytest.raises(CockpitV2AssetNotFoundError):
        load_web_asset(f"{javascript}.gz")
    with pytest.raises(CockpitV2AssetNotFoundError):
        load_web_asset("../manifest.json")


def test_web_is_only_packaged_shell_and_legacy_routes_are_removed(tmp_path):
    client = _client(tmp_path)

    default_redirect = client.get("/", follow_redirects=False)
    cockpit_default = client.get("/")
    legacy_root = client.get("/legacy", follow_redirects=False)
    legacy_nested = client.get("/legacy/runs/run_123", follow_redirects=False)
    cockpit = client.get("/web/work/session_123")
    coding_agents = client.get("/web/coding-agents")
    unknown = client.get("/web/unknown")

    assert default_redirect.status_code == 307
    assert default_redirect.headers["location"] == "/web/work"
    assert default_redirect.headers["cache-control"] == "no-cache"
    assert cockpit_default.status_code == 200
    assert "<title>GigaLoom</title>" in cockpit_default.text
    assert legacy_root.status_code == 404
    assert legacy_nested.status_code == 404
    assert cockpit.status_code == 200
    assert "<title>GigaLoom</title>" in cockpit.text
    assert cockpit.headers["content-security-policy"].startswith(
        "default-src 'none'; script-src 'self'; style-src 'self'"
    )
    assert "frame-src 'self'" in cockpit.headers["content-security-policy"]
    assert "manifest-src 'self'" in cockpit.headers["content-security-policy"]
    assert cockpit.headers["x-content-type-options"] == "nosniff"
    assert coding_agents.status_code == 200
    assert "<title>GigaLoom</title>" in coding_agents.text
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "retired_path",
    (
        "/work",
        "/work/session_123",
        "/runs/run_123",
        "/workflows/workflow_123",
        "/scheduled/schedule_123",
        "/agents",
        "/arena",
        "/evaluate",
        "/tools",
        "/approvals",
    ),
)
def test_retired_default_deep_links_are_not_public_aliases(retired_path, tmp_path):
    response = _client(tmp_path).get(retired_path, follow_redirects=False)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    (
        "/web/automation/agents",
        "/web/automation/workflows",
        "/web/automation/schedules",
        "/web/evaluation/arena",
        "/web/evaluation/evals",
        "/web/evaluation/baselines",
        "/web/plugins/all",
        "/web/plugins/mcp",
        "/web/plugins/plugins",
        "/web/plugins/skills",
        "/web/integrations/harnesses",
        "/web/integrations/models",
        "/web/integrations/mcp",
        "/web/integrations/add",
        "/web/integrations/doctor",
    ),
)
def test_web_remaining_surface_deep_links_are_preserved(path, tmp_path):
    response = _client(tmp_path).get(path)

    assert response.status_code == 200
    assert "<title>GigaLoom</title>" in response.text


def test_web_serves_negotiated_immutable_assets(tmp_path):
    client = _client(tmp_path)
    manifest = load_web_manifest()
    javascript = next(name for name in manifest.initial if name.endswith(".js"))

    response = client.get(
        f"/web/assets/{javascript}",
        headers={"Accept-Encoding": "gzip, br"},
    )
    identity = client.get(
        f"/web/assets/{javascript}",
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["vary"] == "Accept-Encoding"
    assert identity.status_code == 200
    assert "content-encoding" not in identity.headers
    assert len(identity.content) == manifest.assets[javascript].byte_count
    assert client.get(f"/web/assets/{javascript}.br").status_code == 404
    assert client.get("/web/assets/../manifest.json").status_code == 404


def test_web_serves_uncompressed_fonts_when_browser_accepts_brotli(tmp_path):
    client = _client(tmp_path)
    manifest = load_web_manifest()
    font = next(name for name in manifest.assets if name.endswith(".woff2"))

    response = client.get(
        f"/web/assets/{font}",
        headers={"Accept-Encoding": "gzip, br"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert "content-encoding" not in response.headers
    assert len(response.content) == manifest.assets[font].byte_count


def test_web_manifest_failure_has_package_recovery_guidance(monkeypatch, tmp_path):
    from gigaloom.ui import routers
    from gigaloom.ui.web import CockpitV2UnavailableError

    def unavailable() -> str:
        raise CockpitV2UnavailableError("missing")

    monkeypatch.setattr(routers.shell, "load_web_shell", unavailable)
    client = _client(tmp_path)

    failed = client.get("/web/work")

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": (
            "Cockpit packaged assets are unavailable. Reinstall the "
            "gigaloom package or restore its verified build artifact."
        )
    }
    assert client.get("/legacy", follow_redirects=False).status_code == 404
    saved_link = client.get("/workflows/review-team", follow_redirects=False)
    assert saved_link.status_code == 404


def test_web_manifest_is_content_free():
    manifest = load_web_manifest()
    serialized = json.dumps(
        {
            "entry": manifest.entry,
            "initial": manifest.initial,
            "assets": sorted(manifest.assets),
        }
    )

    for forbidden in ("prompt", "message", "token", "/Users/", "credential"):
        assert forbidden not in serialized
