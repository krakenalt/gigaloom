"""Root inventory, preview, and local manifest projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from gigaloom.skills.builtin import (
    BUILTIN_SKILL_SOURCE_ID,
    get_builtin_skill_bundle,
)
from gigaloom.skills.external import parse_external_skill
from gigaloom.skills.portable import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    GEMINI_SKILL_TARGET_ID,
    PortableSkill,
)


from gigaloom.skills.library_models import (
    MAX_PREVIEW_CHARS,
    MAX_ROOT_PLUGINS,
    MAX_ROOT_SKILLS,
    _RootPlugin,
    _RootSkill,
)
from gigaloom.skills.library_support import (
    _safe_relative_path,
    _unsafe_path,
)


class _LibraryInventoryMixin:
    """Root Skill and plugin inventory behavior."""

    def root_skills(self) -> list[dict[str, Any]]:
        """Return deduplicated global/native Skill metadata without instructions."""
        return [_root_projection(item) for item in self._scan_root_skills()]

    def root_plugins(self) -> list[dict[str, Any]]:
        """Return bounded OpenAI-bundled Codex plugin manifest metadata."""
        return [_root_plugin_projection(item) for item in self._scan_root_plugins()]

    def preview(self, preview_id: str) -> dict[str, Any]:
        """Return bounded Skill markdown only after an explicit item selection."""
        if preview_id.startswith("root:"):
            item = next(
                (skill for skill in self._scan_root_skills() if skill.id == preview_id),
                None,
            )
            if item is None:
                raise KeyError(preview_id)
            markdown = item.path.read_text(encoding="utf-8")
            return _preview_projection(
                item.name, item.description, markdown, item.origin, item.target_ids
            )
        if preview_id.startswith("catalog:"):
            catalog_id = preview_id.removeprefix("catalog:")
            entry = self.catalog.get(catalog_id)
            if entry is None or entry.package is None:
                raise KeyError(preview_id)
            if entry.source_id == BUILTIN_SKILL_SOURCE_ID:
                skill = get_builtin_skill_bundle(entry.package_id).skill
                return _preview_projection(
                    skill.name,
                    skill.description,
                    _portable_skill_markdown(skill),
                    "built-in",
                    tuple(item.target_id for item in entry.package.compatibility),
                )
            digest = entry.package.checksum.removeprefix("sha256:")
            skill = parse_external_skill(self.external_store.resolve(digest))
            return _preview_projection(
                skill.name,
                skill.description,
                _portable_skill_markdown(skill),
                entry.source_id,
                tuple(item.target_id for item in entry.package.compatibility),
            )
        if preview_id.startswith("git:"):
            candidate = self._load_candidate(preview_id.removeprefix("git:"))
            if candidate.get("type") != "skill":
                raise KeyError(preview_id)
            relative_dir = _safe_relative_path(str(candidate["relative_dir"]))
            path = (
                self._existing_snapshot_root(str(candidate["snapshot_id"]))
                / relative_dir
                / "SKILL.md"
            )
            markdown = path.read_text(encoding="utf-8")
            return _preview_projection(
                str(candidate["title"]),
                str(candidate["description"]),
                markdown,
                str(candidate["repository_url"]),
                (
                    CODEX_SKILL_TARGET_ID,
                    CLAUDE_SKILL_TARGET_ID,
                    GEMINI_SKILL_TARGET_ID,
                ),
            )
        raise KeyError(preview_id)

    def _scan_root_skills(self) -> tuple[_RootSkill, ...]:
        by_key: dict[tuple[str, str], _RootSkill] = {}
        for root, target_ids, origin in self._root_skill_roots:
            root = Path(root).expanduser()
            if not root.is_dir() or root.is_symlink():
                continue
            count = 0
            for skill_md in sorted(root.rglob("SKILL.md")):
                if count >= MAX_ROOT_SKILLS:
                    break
                if _unsafe_path(root, skill_md):
                    continue
                try:
                    name, description = _skill_metadata(skill_md)
                    digest = hashlib.sha256(skill_md.read_bytes()).hexdigest()
                except (OSError, UnicodeError, ValueError):
                    continue
                count += 1
                key = (name, digest)
                current = by_key.get(key)
                merged_targets = tuple(
                    sorted(set(target_ids).union(current.target_ids if current else ()))
                )
                by_key[key] = _RootSkill(
                    id=f"root:{digest}",
                    name=name,
                    description=description,
                    path=skill_md,
                    target_ids=merged_targets,
                    origin=current.origin if current is not None else origin,
                )
        return tuple(sorted(by_key.values(), key=lambda item: item.name.casefold()))

    def _scan_root_plugins(self) -> tuple[_RootPlugin, ...]:
        by_name: dict[str, _RootPlugin] = {}
        for root, origin in self._root_plugin_roots:
            root = Path(root).expanduser()
            if not root.is_dir() or root.is_symlink():
                continue
            count = 0
            for manifest_path in sorted(root.rglob(".codex-plugin/plugin.json")):
                if count >= MAX_ROOT_PLUGINS:
                    break
                if _unsafe_path(root, manifest_path):
                    continue
                try:
                    plugin = _root_plugin_from_manifest(
                        manifest_path,
                        origin=origin,
                    )
                except (OSError, UnicodeError, ValueError):
                    continue
                count += 1
                current = by_name.get(plugin.name)
                if current is None or plugin.version > current.version:
                    by_name[plugin.name] = plugin
        return tuple(sorted(by_name.values(), key=lambda item: item.title.casefold()))


def _default_root_skill_roots() -> tuple[tuple[Path, tuple[str, ...], str], ...]:
    all_targets = (
        CODEX_SKILL_TARGET_ID,
        CLAUDE_SKILL_TARGET_ID,
        GEMINI_SKILL_TARGET_ID,
    )
    configured = os.environ.get("GIGA_ROOT_SKILLS_DIRS")
    shared = (
        tuple(Path(item) for item in configured.split(os.pathsep) if item)
        if configured
        else (Path.home() / ".agents" / "skills",)
    )
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    gemini_home = Path(os.environ.get("GEMINI_CLI_HOME", Path.home() / ".gemini"))
    roots = [(path, all_targets, "root") for path in shared]
    roots.extend(
        (
            (codex_home / "skills", (CODEX_SKILL_TARGET_ID,), "codex-root"),
            (claude_home / "skills", (CLAUDE_SKILL_TARGET_ID,), "claude-root"),
            (gemini_home / "skills", (GEMINI_SKILL_TARGET_ID,), "gemini-root"),
        )
    )
    return tuple(roots)


def _default_root_plugin_roots() -> tuple[tuple[Path, str], ...]:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    cache = codex_home / "plugins" / "cache"
    return (
        (cache / "openai-primary-runtime", "openai-primary-runtime"),
        (cache / "openai-bundled", "openai-bundled"),
    )


def _root_projection(item: _RootSkill) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "target_ids": list(item.target_ids),
        "origin": item.origin,
        "scope": "root",
        "connected": True,
        "preview_id": item.id,
    }


def _root_plugin_projection(item: _RootPlugin) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "title": item.title,
        "description": item.description,
        "version": item.version,
        "target_ids": list(item.target_ids),
        "origin": item.origin,
        "source_label": "OpenAI",
        "scope": "system",
        "connected": True,
        "invocation": item.invocation,
        "bundled_skills": list(item.bundled_skills),
        "default_prompts": list(item.default_prompts),
        "repository_url": item.repository_url,
    }


def _root_plugin_from_manifest(
    manifest_path: Path,
    *,
    origin: str,
) -> _RootPlugin:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("plugin manifest must be an object")
    name = str(payload.get("name") or "").strip()
    version = str(payload.get("version") or "").strip()
    if not name or not version:
        raise ValueError("plugin manifest identity is incomplete")
    raw_interface = payload.get("interface")
    interface = raw_interface if isinstance(raw_interface, Mapping) else {}
    title = str(interface.get("displayName") or name).strip()
    description = str(
        interface.get("shortDescription") or payload.get("description") or title
    ).strip()
    skills_path = payload.get("skills")
    bundled_skills: list[str] = []
    if isinstance(skills_path, str) and skills_path.strip():
        plugin_root = manifest_path.parent.parent.resolve()
        skills_root = (plugin_root / skills_path).resolve()
        try:
            skills_root.relative_to(plugin_root)
        except ValueError as exc:
            raise ValueError("plugin skills path escapes the plugin") from exc
        if skills_root.is_dir() and not skills_root.is_symlink():
            for skill_md in sorted(skills_root.rglob("SKILL.md"))[:MAX_ROOT_SKILLS]:
                if _unsafe_path(skills_root, skill_md):
                    continue
                try:
                    skill_name, _ = _skill_metadata(skill_md)
                except (OSError, UnicodeError, ValueError):
                    continue
                bundled_skills.append(skill_name)
    default_prompts = interface.get("defaultPrompt")
    prompts = (
        tuple(
            str(item).strip()
            for item in default_prompts[:3]
            if isinstance(item, str) and item.strip()
        )
        if isinstance(default_prompts, list)
        else ()
    )
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    repository_url = payload.get("repository")
    return _RootPlugin(
        id=f"plugin:{digest}",
        name=name,
        title=title,
        description=description,
        version=version,
        target_ids=("codex-plugin",),
        origin=origin,
        invocation=f"@{name}",
        bundled_skills=tuple(sorted(set(bundled_skills))),
        default_prompts=prompts,
        repository_url=(
            repository_url
            if isinstance(repository_url, str) and repository_url.startswith("https://")
            else None
        ),
    )


def _preview_projection(
    name: str,
    description: str,
    markdown: str,
    source: str,
    target_ids: Sequence[str],
) -> dict[str, Any]:
    truncated = len(markdown) > MAX_PREVIEW_CHARS
    return {
        "name": name,
        "description": description,
        "markdown": markdown[:MAX_PREVIEW_CHARS],
        "truncated": truncated,
        "source": source,
        "target_ids": list(target_ids),
    }


def _skill_metadata(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    metadata, _ = _skill_document(text)
    return str(metadata["name"]), str(metadata["description"])


def _skill_document(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Skill frontmatter is required")
    end = text.index("\n---\n", 4)

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate Skill frontmatter key")
        return dict(pairs)

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping
    )
    try:
        value = yaml.load(text[4:end], Loader=UniqueLoader) or {}
    except ValueError:
        raise
    except yaml.YAMLError as exc:
        raise ValueError("Skill frontmatter is invalid") from exc
    if not isinstance(value, Mapping):
        raise ValueError("Skill frontmatter must be an object")
    name = value.get("name")
    description = value.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Skill name is required")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Skill description is required")
    return {"name": name.strip(), "description": description.strip()}, text[end + 5 :]


def _normalize_skill_markdown(text: str) -> str:
    metadata, instructions = _skill_document(text)
    header = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{header}\n---\n\n{instructions.lstrip()}"


def _portable_skill_markdown(skill: PortableSkill) -> str:
    header = yaml.safe_dump(
        {"name": skill.name, "description": skill.description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{header}\n---\n\n{skill.instructions}"
