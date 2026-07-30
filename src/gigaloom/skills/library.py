"""Root Skill inventory, federated search, and immutable Git imports."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path


from gigaloom.skills.external import ExternalSkillStore
from gigaloom.federated_catalog import (
    FederatedCatalogSource,
)
from gigaloom.integration_catalog import (
    IntegrationCatalogStore,
)


from gigaloom.skills.library_federated import (
    _LibraryFederatedMixin,
    _default_sources,
)
from gigaloom.skills.library_git import _LibraryGitMixin
from gigaloom.skills.library_inventory import (
    _LibraryInventoryMixin,
    _default_root_plugin_roots,
    _default_root_skill_roots,
)
from gigaloom.skills.library_models import (
    GitCommandResult,
    GitCommandRunner,
)
from gigaloom.skills.library_support import _run_git


class SkillLibraryService(
    _LibraryInventoryMixin,
    _LibraryFederatedMixin,
    _LibraryGitMixin,
):
    """Project safe Skill discovery without mutating native user homes."""

    def __init__(
        self,
        data_dir: Path,
        *,
        root_skill_roots: Sequence[tuple[Path, Sequence[str], str]] | None = None,
        root_plugin_roots: Sequence[tuple[Path, str]] | None = None,
        federated_sources: Sequence[FederatedCatalogSource] | None = None,
        git_runner: GitCommandRunner | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.catalog = IntegrationCatalogStore(self.data_dir)
        self.external_store = ExternalSkillStore(
            self.data_dir / "integrations" / "external-skills"
        )
        self.git_cache = self.data_dir / "integrations" / "git-snapshots"
        self._root_skill_roots = tuple(
            root_skill_roots
            if root_skill_roots is not None
            else _default_root_skill_roots()
        )
        self._root_plugin_roots = tuple(
            root_plugin_roots
            if root_plugin_roots is not None
            else _default_root_plugin_roots()
        )
        use_default_sources = federated_sources is None
        self._federated_sources = tuple(
            federated_sources if federated_sources is not None else _default_sources()
        )
        self._unconfigured_source_ids = (
            ("skills-sh",)
            if use_default_sources and not os.environ.get("GIGA_SKILLS_PROXY_ORIGIN")
            else ()
        )
        self._git_runner = git_runner or _run_git


__all__ = ["GitCommandResult", "SkillLibraryService"]
