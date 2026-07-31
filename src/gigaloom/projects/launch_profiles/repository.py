"""Authoritative filesystem repository for project launch profiles."""

from __future__ import annotations

import os
from pathlib import Path

from gigaloom.projects.catalog.errors import (
    ProjectCatalogCapacityError,
    ProjectCatalogConflictError,
    ProjectCatalogNotFoundError,
)
from gigaloom.projects.catalog.repository import _exclusive_file_lock, _fsync_directory

from .codec import decode_launch_profile, encode_launch_profile
from .models import (
    MAX_LAUNCH_PROFILES,
    MAX_LAUNCH_PROFILE_PAGE_SIZE,
    LaunchProfilePageV1,
    ProjectLaunchProfileV1,
)


class FilesystemLaunchProfileRepository:
    """Persist one canonical JSON file per soft launch profile."""

    def __init__(self, profiles_dir: str | Path) -> None:
        self.profiles_dir = Path(profiles_dir).expanduser()
        self.entries_dir = self.profiles_dir / "entries"
        self.lock_path = self.profiles_dir / "profiles"

    def create(self, profile: ProjectLaunchProfileV1) -> ProjectLaunchProfileV1:
        """Create one new launch profile."""
        with _exclusive_file_lock(self.lock_path):
            existing = self._read_all()
            if any(
                item.launch_profile_id == profile.launch_profile_id for item in existing
            ):
                raise ProjectCatalogConflictError(
                    f"launch profile already exists: {profile.launch_profile_id}"
                )
            if len(existing) >= MAX_LAUNCH_PROFILES:
                raise ProjectCatalogCapacityError("launch profile capacity exceeded")
            self._write(profile)
        return profile

    def get(self, launch_profile_id: str) -> ProjectLaunchProfileV1:
        """Return one launch profile by stable id."""
        path = self._entry_path(launch_profile_id)
        try:
            return decode_launch_profile(path.read_bytes())
        except FileNotFoundError as exc:
            raise ProjectCatalogNotFoundError(launch_profile_id) from exc

    def replace(
        self,
        profile: ProjectLaunchProfileV1,
        *,
        expected_revision: int,
    ) -> ProjectLaunchProfileV1:
        """Replace one profile using optimistic revision control."""
        with _exclusive_file_lock(self.lock_path):
            current = self.get(profile.launch_profile_id)
            if current.revision != expected_revision:
                raise ProjectCatalogConflictError("launch profile revision is stale")
            if profile.revision != expected_revision + 1:
                raise ProjectCatalogConflictError(
                    "launch profile revision must advance exactly once"
                )
            if profile.catalog_project_id != current.catalog_project_id:
                raise ProjectCatalogConflictError(
                    "launch profile project binding is immutable"
                )
            self._write(profile)
        return profile

    def delete(
        self,
        launch_profile_id: str,
        *,
        expected_revision: int,
    ) -> ProjectLaunchProfileV1:
        """Delete only launch-profile metadata at the presented revision."""
        with _exclusive_file_lock(self.lock_path):
            current = self.get(launch_profile_id)
            if current.revision != expected_revision:
                raise ProjectCatalogConflictError("launch profile revision is stale")
            self._entry_path(launch_profile_id).unlink()
            _fsync_directory(self.entries_dir)
        return current

    def list_page(
        self,
        catalog_project_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LaunchProfilePageV1:
        """Return one bounded stable profile page for a catalog project."""
        if not 1 <= limit <= MAX_LAUNCH_PROFILE_PAGE_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_LAUNCH_PROFILE_PAGE_SIZE}"
            )
        profiles = tuple(
            profile
            for profile in self._read_all()
            if profile.catalog_project_id == catalog_project_id
            and (cursor is None or profile.launch_profile_id > cursor)
        )
        selected = profiles[: limit + 1]
        has_more = len(selected) > limit
        items = selected[:limit]
        return LaunchProfilePageV1(
            items=items,
            next_cursor=items[-1].launch_profile_id if has_more and items else None,
            has_more=has_more,
        )

    def _read_all(self) -> tuple[ProjectLaunchProfileV1, ...]:
        if not self.entries_dir.exists():
            return ()
        paths = sorted(self.entries_dir.glob("launch_*.json"))
        if len(paths) > MAX_LAUNCH_PROFILES:
            raise ProjectCatalogCapacityError("launch profile capacity exceeded")
        return tuple(decode_launch_profile(path.read_bytes()) for path in paths)

    def _entry_path(self, launch_profile_id: str) -> Path:
        if (
            not launch_profile_id.startswith("launch_")
            or len(launch_profile_id) != 31
            or not launch_profile_id[7:].isalnum()
        ):
            raise ValueError("invalid launch_profile_id")
        return self.entries_dir / f"{launch_profile_id}.json"

    def _write(self, profile: ProjectLaunchProfileV1) -> None:
        path = self._entry_path(profile.launch_profile_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encode_launch_profile(profile))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
