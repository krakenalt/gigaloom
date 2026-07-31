#!/usr/bin/env python3
"""Validate one manifest-bound GigaLoom multi-registry release identity."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence


SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
RELEASE_RE = re.compile(
    r"(?P<base>\d+\.\d+\.\d+)"
    r"(?:-(?P<stage>alpha|beta|rc)\.(?P<number>[1-9]\d*))?"
)
EXPECTED_DISTRIBUTION = "gigaloom"
EXPECTED_NPM_PACKAGE = "@gigaloom/web"
EXPECTED_MANIFEST_FIELDS = {
    "git_tag",
    "npm_package",
    "npm_version",
    "python_distribution",
    "python_version",
    "release",
}
_TAG_MARKER = "v"
LEGACY_TAG_PREFIXES = (
    f"gigaloom-{_TAG_MARKER}",
    f"gpt2giga-harness-{_TAG_MARKER}",
)


class ReleaseGuardError(RuntimeError):
    """Raised when release identity or ancestry is unsafe."""


def _json_object(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseGuardError(f"{label} is missing or malformed") from exc
    if not isinstance(payload, Mapping):
        raise ReleaseGuardError(f"{label} must be a JSON object")
    return payload


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        diagnostic = result.stderr.strip() or result.stdout.strip()
        raise ReleaseGuardError(
            f"git {' '.join(arguments)} failed: {diagnostic or result.returncode}"
        )
    return result.stdout.strip()


def _require_ancestor(root: Path, ancestor: str, descendant: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        raise ReleaseGuardError(f"{ancestor} is not an ancestor of {descendant}")
    if result.returncode:
        diagnostic = result.stderr.strip() or result.stdout.strip()
        raise ReleaseGuardError(
            f"cannot verify ancestry {ancestor}..{descendant}: "
            f"{diagnostic or result.returncode}"
        )


def _expected_python_version(release: str) -> str:
    match = RELEASE_RE.fullmatch(release)
    if match is None:
        raise ReleaseGuardError(f"release {release!r} is not supported SemVer")
    stage = match.group("stage")
    number = match.group("number")
    if stage is None:
        return match.group("base")
    python_stage = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
    return f"{match.group('base')}{python_stage}{number}"


def _release_channels(release: str) -> dict[str, str]:
    match = RELEASE_RE.fullmatch(release)
    if match is None:
        raise ReleaseGuardError(f"release {release!r} is not supported SemVer")
    is_prerelease = match.group("stage") is not None
    return {
        "is_prerelease": "true" if is_prerelease else "false",
        "npm_dist_tag": "next" if is_prerelease else "latest",
    }


def _release_identity(
    *,
    release_version_path: Path,
    release_manifest_path: Path,
    python_metadata_path: Path,
    npm_metadata_path: Path,
) -> dict[str, str]:
    try:
        canonical = tomllib.loads(release_version_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseGuardError(
            "canonical release identity is missing or malformed"
        ) from exc
    if set(canonical) != {"version"} or not isinstance(canonical["version"], str):
        raise ReleaseGuardError("canonical release identity must contain only version")
    canonical_release = canonical["version"]
    _expected_python_version(canonical_release)

    manifest = _json_object(release_manifest_path, label="release manifest")
    if set(manifest) != EXPECTED_MANIFEST_FIELDS:
        raise ReleaseGuardError("release manifest fields do not match schema v1")
    if any(not isinstance(manifest[field], str) for field in EXPECTED_MANIFEST_FIELDS):
        raise ReleaseGuardError("release manifest values must be strings")
    identity = {field: manifest[field] for field in EXPECTED_MANIFEST_FIELDS}
    release = identity["release"]
    if release != canonical_release:
        raise ReleaseGuardError(
            "release manifest differs from canonical release identity"
        )
    if identity["git_tag"] != f"v{release}":
        raise ReleaseGuardError("release manifest tag must be the standard v<release>")
    if identity["npm_version"] != release:
        raise ReleaseGuardError("npm version must equal the canonical release")
    if identity["python_version"] != _expected_python_version(release):
        raise ReleaseGuardError(
            "Python version does not map from the canonical release"
        )
    if identity["python_distribution"] != EXPECTED_DISTRIBUTION:
        raise ReleaseGuardError(
            f"Python distribution must be {EXPECTED_DISTRIBUTION!r}"
        )
    if identity["npm_package"] != EXPECTED_NPM_PACKAGE:
        raise ReleaseGuardError(f"npm package must be {EXPECTED_NPM_PACKAGE!r}")

    try:
        project = tomllib.loads(python_metadata_path.read_text(encoding="utf-8"))[
            "project"
        ]
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseGuardError(
            "Python project metadata is missing or malformed"
        ) from exc
    if (
        project.get("name") != identity["python_distribution"]
        or project.get("version") != identity["python_version"]
    ):
        raise ReleaseGuardError(
            "Python project metadata does not match release manifest"
        )

    npm = _json_object(npm_metadata_path, label="npm package metadata")
    if (
        npm.get("name") != identity["npm_package"]
        or npm.get("version") != identity["npm_version"]
        or npm.get("private") is not False
        or not isinstance(npm.get("publishConfig"), Mapping)
        or npm["publishConfig"].get("access") != "public"
    ):
        raise ReleaseGuardError("npm package metadata does not match release manifest")
    return identity


def validate_release(
    *,
    root: Path,
    policy_path: Path,
    release_version_path: Path,
    release_manifest_path: Path,
    python_metadata_path: Path,
    npm_metadata_path: Path,
    event_name: str,
    repository: str,
    ref: str,
    commit: str,
    release_tag: str,
    release_target: str,
    main_ref: str,
) -> dict[str, str]:
    """Return guarded release metadata or raise a fail-closed error."""
    policy = _json_object(policy_path, label="release policy")
    if set(policy) != {"default_branch", "history_floor", "repository"}:
        raise ReleaseGuardError("release policy fields do not match schema v2")
    if repository != policy["repository"]:
        raise ReleaseGuardError(
            f"repository {repository!r} is not the target {policy['repository']!r}"
        )
    identity = _release_identity(
        release_version_path=release_version_path,
        release_manifest_path=release_manifest_path,
        python_metadata_path=python_metadata_path,
        npm_metadata_path=npm_metadata_path,
    )
    if SHA_RE.fullmatch(commit) is None:
        raise ReleaseGuardError("commit must be a full lowercase Git SHA")
    if _git(root, "rev-parse", "HEAD^{commit}") != commit:
        raise ReleaseGuardError("checked-out HEAD does not match the requested SHA")
    history_floor = policy.get("history_floor")
    if not isinstance(history_floor, str) or SHA_RE.fullmatch(history_floor) is None:
        raise ReleaseGuardError("history floor must be a full lowercase Git SHA")
    _require_ancestor(root, history_floor, commit)
    _require_ancestor(root, commit, main_ref)

    expected_tag = identity["git_tag"]
    default_branch = policy.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise ReleaseGuardError("default branch must be a non-empty string")
    if event_name == "workflow_dispatch":
        expected_ref = f"refs/heads/{default_branch}"
        if ref != expected_ref:
            raise ReleaseGuardError(
                f"candidate build must run from {expected_ref}, not {ref}"
            )
        if release_tag or release_target:
            raise ReleaseGuardError("candidate build cannot carry release metadata")
        if _git(root, "rev-parse", f"{main_ref}^{{commit}}") != commit:
            raise ReleaseGuardError("candidate build must use the current main tip")
        mode = "candidate"
    elif event_name in {"candidate", "publish", "release"}:
        if release_tag.startswith(LEGACY_TAG_PREFIXES):
            raise ReleaseGuardError("legacy release tag prefixes are forbidden")
        if release_tag != expected_tag:
            raise ReleaseGuardError(
                f"release tag {release_tag!r} must equal {expected_tag!r}"
            )
        if ref != f"refs/tags/{expected_tag}":
            raise ReleaseGuardError("release ref and manifest tag do not agree")
        if release_target != default_branch:
            raise ReleaseGuardError("release target must be the default branch")
        tag_commit = _git(root, "rev-parse", f"refs/tags/{expected_tag}^{{commit}}")
        if tag_commit != commit:
            raise ReleaseGuardError("release tag and checked-out commit do not agree")
        if (
            event_name == "candidate"
            and _git(root, "rev-parse", f"{main_ref}^{{commit}}") != commit
        ):
            raise ReleaseGuardError("candidate tag must use the current main tip")
        mode = {
            "candidate": "candidate",
            "publish": "publish",
            "release": "tagged",
        }[event_name]
    else:
        raise ReleaseGuardError(f"unsupported event {event_name!r}")

    return {
        "commit": commit,
        **_release_channels(identity["release"]),
        "mode": mode,
        "npm_package": identity["npm_package"],
        "npm_version": identity["npm_version"],
        "python_distribution": identity["python_distribution"],
        "python_version": identity["python_version"],
        "release": identity["release"],
        "tag": expected_tag,
        "version": identity["python_version"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Validate CLI inputs and optionally emit GitHub Actions outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--release-version", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--python-metadata", type=Path, required=True)
    parser.add_argument("--npm-metadata", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--release-target", default="")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    try:
        result = validate_release(
            root=args.root.resolve(),
            policy_path=args.policy.resolve(),
            release_version_path=args.release_version.resolve(),
            release_manifest_path=args.release_manifest.resolve(),
            python_metadata_path=args.python_metadata.resolve(),
            npm_metadata_path=args.npm_metadata.resolve(),
            event_name=args.event_name,
            repository=args.repository,
            ref=args.ref,
            commit=args.commit,
            release_tag=args.release_tag,
            release_target=args.release_target,
            main_ref=args.main_ref,
        )
    except (OSError, ValueError, ReleaseGuardError) as error:
        parser.error(str(error))

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in sorted(result.items()):
                print(f"{key}={value}", file=output)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
