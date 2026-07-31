import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_guard.py"
LEGACY_TAG_PREFIX = f"gigaloom-{'v'}"


def load_release_guard_module():
    spec = importlib.util.spec_from_file_location("release_guard", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def release_repository(tmp_path: Path) -> dict[str, str | Path]:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Release Test")
    git(root, "config", "user.email", "release@example.test")

    (root / "HISTORY").write_text("old repository history\n", encoding="utf-8")
    git(root, "add", "HISTORY")
    git(root, "commit", "-m", "historical source")
    historical = git(root, "rev-parse", "HEAD")

    (root / "STANDALONE").write_text("standalone repository\n", encoding="utf-8")
    git(root, "add", "STANDALONE")
    git(root, "commit", "-m", "standalone history floor")
    floor = git(root, "rev-parse", "HEAD")

    python_metadata = root / "pyproject.toml"
    python_metadata.write_text(
        '[project]\nname = "gigaloom"\nversion = "0.6.0a1"\n',
        encoding="utf-8",
    )
    web = root / "web"
    web.mkdir()
    npm_metadata = web / "package.json"
    npm_metadata.write_text(
        json.dumps(
            {
                "name": "@gigaloom/web",
                "private": False,
                "publishConfig": {"access": "public"},
                "version": "0.6.0-alpha.1",
            }
        ),
        encoding="utf-8",
    )
    release = root / "release"
    release.mkdir()
    release_manifest = release / "release.json"
    release_manifest.write_text(
        json.dumps(
            {
                "git_tag": "v0.6.0-alpha.1",
                "npm_package": "@gigaloom/web",
                "npm_version": "0.6.0-alpha.1",
                "python_distribution": "gigaloom",
                "python_version": "0.6.0a1",
                "release": "0.6.0-alpha.1",
            }
        ),
        encoding="utf-8",
    )
    policy = root / "release-policy.json"
    policy.write_text(
        json.dumps(
            {
                "default_branch": "main",
                "history_floor": floor,
                "repository": "krakenalt/gigaloom",
            }
        ),
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "release identity")
    commit = git(root, "rev-parse", "HEAD")
    tag = "v0.6.0-alpha.1"
    git(root, "tag", tag)
    return {
        "commit": commit,
        "floor": floor,
        "historical": historical,
        "npm_metadata": npm_metadata,
        "policy": policy,
        "python_metadata": python_metadata,
        "release_manifest": release_manifest,
        "root": root,
        "tag": tag,
    }


def validate(module, repository_data, **overrides):
    values = {
        "root": repository_data["root"],
        "policy_path": repository_data["policy"],
        "release_manifest_path": repository_data["release_manifest"],
        "python_metadata_path": repository_data["python_metadata"],
        "npm_metadata_path": repository_data["npm_metadata"],
        "event_name": "release",
        "repository": "krakenalt/gigaloom",
        "ref": f"refs/tags/{repository_data['tag']}",
        "commit": repository_data["commit"],
        "release_tag": repository_data["tag"],
        "release_target": "main",
        "main_ref": "main",
    }
    values.update(overrides)
    return module.validate_release(**values)


def test_release_guard_accepts_exact_release_and_candidate(tmp_path: Path):
    module = load_release_guard_module()
    repository = release_repository(tmp_path)

    assert validate(module, repository) == {
        "commit": repository["commit"],
        "mode": "tagged",
        "npm_package": "@gigaloom/web",
        "npm_version": "0.6.0-alpha.1",
        "python_distribution": "gigaloom",
        "python_version": "0.6.0a1",
        "release": "0.6.0-alpha.1",
        "tag": "v0.6.0-alpha.1",
        "version": "0.6.0a1",
    }
    candidate = validate(
        module,
        repository,
        event_name="workflow_dispatch",
        ref="refs/heads/main",
        release_tag="",
        release_target="",
    )
    assert candidate["mode"] == "candidate"
    assert candidate["commit"] == repository["commit"]
    publish = validate(module, repository, event_name="publish")
    assert publish["mode"] == "publish"
    assert publish["commit"] == repository["commit"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"repository": "ai-forever/gpt2giga"}, "is not the target"),
        (
            {"release_tag": f"{LEGACY_TAG_PREFIX}0.6.0-alpha.1"},
            "legacy release tag",
        ),
        ({"release_target": "archive"}, "default branch"),
        (
            {
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/release/test",
                "release_tag": "",
                "release_target": "",
            },
            "must run from refs/heads/main",
        ),
    ],
)
def test_release_guard_rejects_wrong_identity(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
):
    module = load_release_guard_module()
    repository = release_repository(tmp_path)

    with pytest.raises(module.ReleaseGuardError, match=message):
        validate(module, repository, **overrides)


def test_release_guard_rejects_pre_standalone_history(tmp_path: Path):
    module = load_release_guard_module()
    repository = release_repository(tmp_path)
    root = repository["root"]
    assert isinstance(root, Path)
    retained_paths = {}
    for key in ("policy", "release_manifest", "python_metadata", "npm_metadata"):
        source = repository[key]
        assert isinstance(source, Path)
        retained = tmp_path / f"{key}{source.suffix}"
        retained.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        retained_paths[f"{key}_path"] = retained
    git(root, "checkout", "--detach", str(repository["historical"]))

    with pytest.raises(module.ReleaseGuardError, match="is not an ancestor"):
        validate(
            module,
            repository,
            commit=repository["historical"],
            main_ref="main",
            **retained_paths,
        )


@pytest.mark.parametrize(
    ("target", "replacement", "message"),
    [
        ("python", "0.6.0a2", "Python project metadata"),
        ("npm", "0.6.0-beta.1", "npm package metadata"),
        ("manifest-python", "0.6.0b1", "does not map"),
        ("manifest-tag", f"{LEGACY_TAG_PREFIX}0.6.0-alpha.1", "standard"),
    ],
)
def test_release_guard_rejects_metadata_drift(
    tmp_path: Path,
    target: str,
    replacement: str,
    message: str,
):
    module = load_release_guard_module()
    repository = release_repository(tmp_path)
    if target == "python":
        path = repository["python_metadata"]
        assert isinstance(path, Path)
        path.write_text(
            f'[project]\nname = "gigaloom"\nversion = "{replacement}"\n',
            encoding="utf-8",
        )
    elif target == "npm":
        path = repository["npm_metadata"]
        assert isinstance(path, Path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = replacement
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path = repository["release_manifest"]
        assert isinstance(path, Path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        field = "python_version" if target == "manifest-python" else "git_tag"
        payload[field] = replacement
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.ReleaseGuardError, match=message):
        validate(module, repository)


def test_release_guard_rejects_unknown_manifest_fields(tmp_path: Path):
    module = load_release_guard_module()
    repository = release_repository(tmp_path)
    manifest = repository["release_manifest"]
    assert isinstance(manifest, Path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["legacy_tag"] = f"{LEGACY_TAG_PREFIX}0.6.0-alpha.1"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.ReleaseGuardError, match="schema v1"):
        validate(module, repository)
