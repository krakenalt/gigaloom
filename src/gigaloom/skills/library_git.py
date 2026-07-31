"""Immutable Git inspection and reviewed Skill import workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any


from gigaloom.skills.external import parse_external_skill
from gigaloom.integration_catalog import (
    CatalogEntry,
    CatalogSourceType,
)
from gigaloom.integration_packages import (
    integration_package_from_dict,
    integration_package_to_dict,
)


from gigaloom.skills.library_inventory import (
    _normalize_skill_markdown,
    _skill_metadata,
)
from gigaloom.skills.library_federated import (
    _federated_metadata_from_candidate,
)
from gigaloom.skills.library_models import (
    GIT_TIMEOUT_SECONDS,
    MAX_GIT_CANDIDATES,
    MAX_PREVIEW_CHARS,
    _GIT_REF_RE,
    _GitSnapshot,
)
from gigaloom.skills.library_support import (
    _artifact_hash,
    _canonical_github_repository,
    _external_skill_package,
    _license_evidence,
    _read_skill_files,
    _safe_relative_path,
    _unsafe_path,
)


class _LibraryGitMixin:
    """Git snapshot, candidate, and import behavior."""

    async def inspect_git(
        self,
        repository_url: str,
        *,
        ref: str | None = None,
        source_id: str | None = None,
        upstream_id: str | None = None,
    ) -> dict[str, Any]:
        """Clone one admitted GitHub ref and inspect bounded install candidates."""
        if (source_id is None) != (upstream_id is None):
            raise ValueError("source_id and upstream_id must be supplied together")
        source_provenance = None
        if source_id is not None and upstream_id is not None:
            detail = await self.source_detail(
                source_id,
                upstream_id,
                include_audit=False,
            )
            source_provenance = detail["provenance"]
            source_repository = source_provenance.get("repository_url")
            if not isinstance(source_repository, str):
                raise ValueError("selected source has no reviewable repository")
            requested_repository, _ = _canonical_github_repository(repository_url)
            canonical_source, _ = _canonical_github_repository(source_repository)
            if requested_repository != canonical_source:
                raise ValueError(
                    "repository does not match the selected source provenance"
                )
        return await asyncio.to_thread(
            self._inspect_git,
            repository_url,
            ref,
            source_provenance,
        )

    def import_git_skill(self, candidate_id: str) -> CatalogEntry:
        """Import one previously inspected Skill into the offline catalog."""
        candidate = self._load_candidate(candidate_id)
        if candidate.get("type") != "skill":
            raise ValueError("selected Git candidate is not a Skill")
        snapshot_root = self._existing_snapshot_root(str(candidate["snapshot_id"]))
        relative_dir = _safe_relative_path(str(candidate["relative_dir"]))
        skill_root = snapshot_root / relative_dir
        files = _read_skill_files(skill_root)
        reviewed_content_hash = candidate.get("reviewed_content_hash")
        if (
            not isinstance(reviewed_content_hash, str)
            or _artifact_hash(files) != reviewed_content_hash
        ):
            raise ValueError("reviewed Git artifact hash drifted")
        source_provenance = candidate.get("source_provenance")
        if source_provenance is not None:
            if not isinstance(source_provenance, Mapping):
                raise ValueError("Git candidate provenance is invalid")
            expected_hash = source_provenance.get("content_hash")
            if not isinstance(expected_hash, str):
                raise ValueError("selected source artifact hash is invalid")
        files["SKILL.md"] = _normalize_skill_markdown(
            files["SKILL.md"].decode("utf-8")
        ).encode("utf-8")
        digest = _artifact_hash(files)
        artifact = self.external_store.import_artifact(
            source_id=str(candidate["repository_url"]),
            immutable_ref=str(candidate["commit"]),
            license_evidence=str(candidate["license"]),
            files=files,
            expected_sha256=digest,
        )
        skill = parse_external_skill(artifact)
        package = _external_skill_package(candidate, skill, artifact.sha256)
        return self.catalog.import_package(
            package,
            source_id=f"git-{str(candidate['snapshot_id'])[:24]}",
            source_type=CatalogSourceType.GIT,
            federated=(
                _federated_metadata_from_candidate(candidate)
                if source_provenance is not None
                else None
            ),
        )

    def _inspect_git(
        self,
        repository_url: str,
        ref: str | None,
        source_provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_url, embedded_ref = _canonical_github_repository(repository_url)
        selected_ref = ref.strip() if ref is not None and ref.strip() else embedded_ref
        if selected_ref is not None and _GIT_REF_RE.fullmatch(selected_ref) is None:
            raise ValueError("Git ref is invalid")
        self.git_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        clone_root = Path(tempfile.mkdtemp(prefix=".inspect-", dir=self.git_cache))
        try:
            argv = [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--filter=blob:none",
            ]
            if selected_ref is not None:
                argv.extend(("--branch", selected_ref))
            argv.extend(("--", canonical_url, str(clone_root / "repo")))
            result = self._git_runner(tuple(argv), None, GIT_TIMEOUT_SECONDS)
            if result.returncode != 0:
                raise ValueError("Git repository could not be inspected")
            repo = clone_root / "repo"
            commit_result = self._git_runner(
                ("git", "rev-parse", "HEAD"), repo, GIT_TIMEOUT_SECONDS
            )
            commit = commit_result.stdout.strip()
            if commit_result.returncode != 0 or not re.fullmatch(
                r"[0-9a-f]{40}", commit
            ):
                raise ValueError(
                    "Git repository did not resolve to an immutable commit"
                )
            snapshot_id = hashlib.sha256(
                f"{canonical_url}\0{commit}".encode()
            ).hexdigest()
            destination = self._snapshot_root(snapshot_id)
            if destination.exists():
                if destination.is_symlink() or not destination.is_dir():
                    raise ValueError("Git snapshot cache is invalid")
                shutil.rmtree(clone_root)
            else:
                os.replace(repo, destination)
                shutil.rmtree(clone_root)
            snapshot = _GitSnapshot(
                repository_url=canonical_url,
                requested_ref=selected_ref,
                commit=commit,
                root=destination,
                source_provenance=source_provenance,
            )
            candidates = self._git_candidates(snapshot, snapshot_id)
            return {
                "repository_url": canonical_url,
                "requested_ref": selected_ref,
                "commit": commit,
                "snapshot_id": snapshot_id,
                "candidates": candidates,
            }
        except Exception:
            if clone_root.exists():
                shutil.rmtree(clone_root)
            raise

    def _git_candidates(
        self, snapshot: _GitSnapshot, snapshot_id: str
    ) -> list[dict[str, Any]]:
        candidates = []
        license_evidence = _license_evidence(snapshot.root)
        source_provenance = (
            dict(snapshot.source_provenance)
            if snapshot.source_provenance is not None
            else None
        )
        source_provenance_sha256 = (
            hashlib.sha256(
                json.dumps(
                    source_provenance,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if source_provenance is not None
            else None
        )
        for skill_md in sorted(snapshot.root.rglob("SKILL.md")):
            if len(candidates) >= MAX_GIT_CANDIDATES:
                break
            if (
                _unsafe_path(snapshot.root, skill_md)
                or skill_md.stat().st_size > MAX_PREVIEW_CHARS * 4
            ):
                continue
            try:
                name, description = _skill_metadata(skill_md)
            except (OSError, UnicodeError, ValueError):
                continue
            relative_dir = str(skill_md.parent.relative_to(snapshot.root)) or "."
            reviewed_content_hash = _artifact_hash(_read_skill_files(skill_md.parent))
            candidate_id = hashlib.sha256(
                (
                    f"{snapshot_id}\0skill\0{relative_dir}\0"
                    f"{source_provenance_sha256 or ''}"
                ).encode()
            ).hexdigest()
            candidate = {
                "id": candidate_id,
                "type": "skill",
                "title": name,
                "description": description,
                "relative_dir": relative_dir,
                "repository_url": snapshot.repository_url,
                "commit": snapshot.commit,
                "snapshot_id": snapshot_id,
                "license": license_evidence,
                "preview_id": f"git:{candidate_id}",
                "manifest": None,
                "source_provenance": source_provenance,
                "source_provenance_sha256": source_provenance_sha256,
                "reviewed_content_hash": reviewed_content_hash,
            }
            self._write_candidate(candidate)
            candidates.append(candidate)
        for manifest_path in sorted(snapshot.root.rglob("integration-package.json")):
            if len(candidates) >= MAX_GIT_CANDIDATES:
                break
            if (
                _unsafe_path(snapshot.root, manifest_path)
                or manifest_path.stat().st_size > 512_000
            ):
                continue
            try:
                manifest = integration_package_to_dict(
                    integration_package_from_dict(
                        json.loads(manifest_path.read_text(encoding="utf-8"))
                    )
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            component_types = {item["type"] for item in manifest["components"]}
            candidate_type = (
                "mcp"
                if "mcp" in component_types
                else "plugin"
                if component_types.intersection({"plugin", "extension"})
                else "package"
            )
            relative_dir = str(manifest_path.parent.relative_to(snapshot.root)) or "."
            candidate_id = hashlib.sha256(
                (
                    f"{snapshot_id}\0manifest\0{relative_dir}\0"
                    f"{source_provenance_sha256 or ''}"
                ).encode()
            ).hexdigest()
            candidate = {
                "id": candidate_id,
                "type": candidate_type,
                "title": manifest["id"],
                "description": "Immutable integration-package.json",
                "relative_dir": relative_dir,
                "repository_url": snapshot.repository_url,
                "commit": snapshot.commit,
                "snapshot_id": snapshot_id,
                "license": manifest["license"],
                "preview_id": None,
                "manifest": manifest,
                "source_provenance": source_provenance,
                "source_provenance_sha256": source_provenance_sha256,
            }
            self._write_candidate(candidate)
            candidates.append(candidate)
        return candidates

    def _candidate_path(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
            raise ValueError("Git candidate id is invalid")
        return self.git_cache / "candidates" / f"{candidate_id}.json"

    def _write_candidate(self, candidate: Mapping[str, Any]) -> None:
        path = self._candidate_path(str(candidate["id"]))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _load_candidate(self, candidate_id: str) -> dict[str, Any]:
        path = self._candidate_path(candidate_id)
        if not path.is_file() or path.is_symlink():
            raise KeyError(candidate_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("id") != candidate_id:
            raise ValueError("Git candidate state is invalid")
        provenance = value.get("source_provenance")
        expected_provenance_hash = value.get("source_provenance_sha256")
        if provenance is not None:
            actual_provenance_hash = hashlib.sha256(
                json.dumps(
                    provenance,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if expected_provenance_hash != actual_provenance_hash:
                raise ValueError("Git candidate provenance binding is invalid")
            candidate_kind = "skill" if value.get("type") == "skill" else "manifest"
            expected_candidate_id = hashlib.sha256(
                (
                    f"{value.get('snapshot_id')}\0{candidate_kind}\0"
                    f"{value.get('relative_dir')}\0{actual_provenance_hash}"
                ).encode()
            ).hexdigest()
            if expected_candidate_id != candidate_id:
                raise ValueError("Git candidate provenance binding is invalid")
        elif expected_provenance_hash is not None:
            raise ValueError("Git candidate provenance binding is invalid")
        return value

    def _snapshot_root(self, snapshot_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
            raise ValueError("Git snapshot id is invalid")
        return self.git_cache / snapshot_id

    def _existing_snapshot_root(self, snapshot_id: str) -> Path:
        root = self._snapshot_root(snapshot_id)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Git snapshot is unavailable")
        return root
