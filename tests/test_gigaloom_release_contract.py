"""Standalone release and Pages guards owned by krakenalt/gigaloom."""

import json
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    with (REPOSITORY_ROOT / ".github/workflows" / name).open(encoding="utf-8") as file:
        return yaml.load(file, Loader=yaml.BaseLoader)


def test_release_candidate_workflow_builds_and_attests_without_publishing():
    workflow = _workflow("publish-pypi.yml")
    assert workflow["on"] == {"workflow_dispatch": ""}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"attest", "candidate"}

    jobs = workflow["jobs"]
    assert jobs["candidate"].get("environment") is None
    assert jobs["candidate"].get("permissions", {}).get("id-token") is None
    assert jobs["attest"].get("environment") is None
    assert jobs["attest"]["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
    }

    text = (REPOSITORY_ROOT / ".github/workflows/publish-pypi.yml").read_text(
        encoding="utf-8"
    )
    assert "uv build --wheel --sdist --no-sources" in text
    assert "npm pack ./web --ignore-scripts --pack-destination" in text
    assert "scripts/verify_release_artifacts.py" in text
    assert text.count("npm --prefix web run build:npm:release") == 1
    assert "--wheel dist/release-candidate/*.whl" in text
    assert "--sdist dist/release-candidate/*.tar.gz" in text
    assert "--npm-tarball dist/release-candidate/*.tgz" in text
    assert "candidate-manifest.json" in text
    assert "SHA256SUMS" in text
    assert "actions/attest-build-provenance@v4" in text
    assert "./scripts/ci-base.sh sync-all-extras" in text
    assert "./scripts/ci-public-gateway.sh" in text
    for forbidden in (
        "gh release",
        "npm publish",
        "uv publish",
        "https://pypi.org/",
        "registry.npmjs.org",
    ):
        assert forbidden not in text


def test_release_policy_freezes_target_identity_and_first_release():
    policy = json.loads(
        (REPOSITORY_ROOT / ".github/release-policy.json").read_text(encoding="utf-8")
    )
    assert policy == {
        "default_branch": "main",
        "history_floor": "5593db1f20839f7bc56d40c880a8d5498a4d3bdc",
        "repository": "krakenalt/gigaloom",
    }
    assert (REPOSITORY_ROOT / "uv.lock").is_file()
    ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "\nuv.lock\n" not in ignore


def test_pages_workflow_and_docusaurus_use_target_project_path():
    workflow = _workflow("docs-pages.yaml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deploy"]["permissions"] == {
        "id-token": "write",
        "pages": "write",
    }

    workflow_text = (REPOSITORY_ROOT / ".github/workflows/docs-pages.yaml").read_text(
        encoding="utf-8"
    )
    assert "packages/gpt2giga/src" not in workflow_text
    assert "actions/upload-pages-artifact@v5" in workflow_text
    assert "path: docs-site/build" in workflow_text

    config = (REPOSITORY_ROOT / "docs-site/docusaurus.config.ts").read_text(
        encoding="utf-8"
    )
    assert "url: 'https://krakenalt.github.io'" in config
    assert "baseUrl: '/gigaloom/'" in config
    assert "organizationName: 'krakenalt'" in config
    assert "projectName: 'gigaloom'" in config


def test_release_recovery_is_fail_closed_and_preserves_immutable_versions():
    recovery = (REPOSITORY_ROOT / ".github/RELEASE_RECOVERY.md").read_text(
        encoding="utf-8"
    )
    for contract in (
        "Candidate builds never publish",
        "one retained\ncandidate artifact",
        "standard `v<release>` tag",
        "protected environments are ready",
        "Published versions are immutable",
        "npm succeeded but PyPI failed",
        "PyPI succeeded but npm failed",
        "never move the tag",
        "previous deployment",
    ):
        assert contract in recovery


def test_release_drafter_cannot_mutate_on_first_push():
    workflow = _workflow("release-drafter.yaml")
    assert workflow["on"] == {"workflow_dispatch": ""}
    assert workflow["permissions"] == {"contents": "write"}
