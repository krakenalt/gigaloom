"""Standalone release and Pages guards owned by krakenalt/gigaloom."""

import json
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    with (REPOSITORY_ROOT / ".github/workflows" / name).open(encoding="utf-8") as file:
        return yaml.load(file, Loader=yaml.BaseLoader)


def test_release_workflow_is_target_only_and_manual_runs_cannot_publish():
    workflow = _workflow("publish-pypi.yml")
    assert workflow["on"] == {
        "release": {"types": ["published"]},
        "workflow_dispatch": "",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {
        "assets",
        "build",
        "metadata",
        "release-assets",
        "trusted-publish",
    }

    jobs = workflow["jobs"]
    assert jobs["trusted-publish"]["environment"] == "pypi-harness"
    assert jobs["trusted-publish"]["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
    }
    for name, job in jobs.items():
        if name != "trusted-publish":
            assert job.get("permissions", {}).get("id-token") is None

    text = (REPOSITORY_ROOT / ".github/workflows/publish-pypi.yml").read_text(
        encoding="utf-8"
    )
    assert "uv build --wheel --sdist --no-sources" in text
    assert "uv build --package gpt2giga " not in text
    legacy_tag_prefix = "gpt2giga-harness-" + "v"
    assert legacy_tag_prefix not in text
    assert "https://pypi.org/pypi/gigaloom/" in text
    assert 'name "gigaloom-${RELEASE_VERSION}-*.whl"' in text
    assert "actions/attest-build-provenance@v4" in text
    assert "uv publish --trusted-publishing always" in text
    assert "assets/_build/licenses.json dist/gigaloom/" in text
    assert "THIRD_PARTY_LICENSES.txt" not in text
    assert "./scripts/ci-base.sh sync-all-extras" in text
    assert "./scripts/ci-public-gateway.sh" in text
    assert text.count("if: github.event_name == 'release'") >= 4
    assert text.index("Fail closed if the public version already exists") < text.index(
        "Upload commit-bound frontend evidence"
    )


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
