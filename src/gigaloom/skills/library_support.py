"""Immutable Git and external Skill package helpers."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit


from gigaloom.integration_packages import (
    InstallationScope,
    IntegrationCompatibility,
    IntegrationComponent,
    IntegrationComponentType,
    IntegrationPackage,
    IntegrationSourceType,
    IntegrationTargetOverlay,
    IntegrationTrustEvidence,
    IntegrationTrustKind,
    IntegrationTrustStatus,
    IntegrationUpdatePolicy,
)
from gigaloom.skills.portable import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    GEMINI_SKILL_TARGET_ID,
    PortableSkill,
)


from gigaloom.skills.library_models import (
    GitCommandResult,
    _GITHUB_PART_RE,
)


def _canonical_github_repository(value: str) -> tuple[str, str | None]:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Git inspection currently accepts public GitHub HTTPS URLs")
    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) < 2:
        raise ValueError("GitHub repository URL is incomplete")
    owner, repository = parts[:2]
    repository = repository.removesuffix(".git")
    if not _GITHUB_PART_RE.fullmatch(owner) or not _GITHUB_PART_RE.fullmatch(
        repository
    ):
        raise ValueError("GitHub repository identity is invalid")
    embedded_ref = None
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] != "tree":
            raise ValueError("GitHub URL must identify a repository or tree ref")
        embedded_ref = "/".join(parts[3:])
    return urlunsplit(
        ("https", "github.com", f"/{owner}/{repository}.git", "", "")
    ), embedded_ref


def _run_git(
    argv: tuple[str, ...], cwd: Path | None, timeout: float
) -> GitCommandResult:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND"}
    }
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitCommandResult(1, "", type(exc).__name__)
    return GitCommandResult(
        completed.returncode,
        completed.stdout[-8_000:],
        completed.stderr[-8_000:],
    )


def _unsafe_path(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return not path.is_file()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("Git candidate path is unsafe")
    return path


def _read_skill_files(root: Path) -> dict[str, bytes]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Git Skill directory is unavailable")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if ".git" in path.relative_to(root).parts:
            continue
        if _unsafe_path(root, path):
            raise ValueError("Git Skill contains an unsafe path")
        relative = str(path.relative_to(root))
        files[relative] = path.read_bytes()
    return files


def _artifact_hash(files: Mapping[str, bytes]) -> str:
    return hashlib.sha256(
        b"".join(name.encode() + b"\0" + files[name] for name in sorted(files))
    ).hexdigest()


def _license_evidence(root: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            return name
    return "operator-review-required"


def _external_skill_package(
    candidate: Mapping[str, Any], skill: PortableSkill, artifact_sha256: str
) -> IntegrationPackage:
    commit = str(candidate["commit"])
    repository_url = str(candidate["repository_url"])
    package_id = "git." + ".".join(
        part.lower().replace("_", "-")
        for part in (
            *urlsplit(repository_url).path.strip("/").removesuffix(".git").split("/"),
            skill.name,
        )
    )
    target_ids = (
        CODEX_SKILL_TARGET_ID,
        CLAUDE_SKILL_TARGET_ID,
        GEMINI_SKILL_TARGET_ID,
    )
    license_name = "NOASSERTION"
    return IntegrationPackage(
        id=package_id,
        version=f"0.0.0+{commit[:12]}",
        publisher=urlsplit(repository_url).path.strip("/").split("/", 1)[0],
        license=license_name,
        source_type=IntegrationSourceType.GIT,
        source=repository_url,
        immutable_ref=commit,
        checksum=f"sha256:{artifact_sha256}",
        components=(
            IntegrationComponent(
                id=skill.component_id,
                type=IntegrationComponentType.SKILL,
                portable=True,
            ),
        ),
        requirements=(),
        overlays=tuple(
            IntegrationTargetOverlay(
                target_id=target_id,
                component_ids=(skill.component_id,),
            )
            for target_id in target_ids
        ),
        compatibility=tuple(
            IntegrationCompatibility(
                target_id=target_id,
                required_capabilities=("skill.discovery",),
            )
            for target_id in target_ids
        ),
        scopes=(InstallationScope.MANAGED_HOME, InstallationScope.PROJECT),
        update_policy=IntegrationUpdatePolicy.PINNED,
        verification_steps=("skill-discovery",),
        rollback_steps=("restore-snapshot",),
        trust_evidence=(
            IntegrationTrustEvidence(
                id="git-immutable-source",
                kind=IntegrationTrustKind.SOURCE,
                status=IntegrationTrustStatus.VERIFIED,
                authority="git",
                revision=commit,
            ),
            IntegrationTrustEvidence(
                id="git-publisher-identity",
                kind=IntegrationTrustKind.PUBLISHER,
                status=IntegrationTrustStatus.UNVERIFIED,
                authority="github",
                revision=commit,
            ),
            IntegrationTrustEvidence(
                id="git-license-review",
                kind=IntegrationTrustKind.LICENSE,
                status=IntegrationTrustStatus.UNVERIFIED,
                authority="operator",
                revision=artifact_sha256,
            ),
        ),
    )
