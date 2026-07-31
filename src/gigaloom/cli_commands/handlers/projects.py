"""CLI handlers for Project Catalog and soft Launch Profile workflows."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from gigaloom.cli_commands.output import print_json
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    ProjectCatalogConflictError,
    ProjectCatalogService,
    ProjectLaunchProfileService,
    ProjectLaunchProfileV1,
    SessionCatalogBindingService,
    project_id_for_root,
    resolved_project_location,
)
from gigaloom.sessions import FilesystemHarnessSessionStore


ProjectCommandHandler = Callable[[argparse.Namespace, HarnessConfig], int]

PROJECT_CATALOG_HANDLERS: dict[str, ProjectCommandHandler] = {}


def project_command_handler(function: ProjectCommandHandler) -> ProjectCommandHandler:
    """Expose a handler to the integration-owned central lazy registry."""
    PROJECT_CATALOG_HANDLERS[function.__name__] = function
    return function


def resolve_project_command_handler(name: str) -> ProjectCommandHandler:
    """Resolve one B2-owned handler without importing the legacy CLI graph."""
    try:
        return PROJECT_CATALOG_HANDLERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown project command handler: {name}") from exc


@project_command_handler
def _handle_project_catalog_list(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, _, _, _ = _project_services(config)
    page = repository.list_page(
        cursor=args.cursor,
        limit=args.limit,
        include_tombstoned=args.include_tombstoned,
    )
    payload = {
        "projects": [asdict(item) for item in page.items],
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
    }
    return _render(args, payload, _project_lines(payload["projects"]))


@project_command_handler
def _handle_project_catalog_add(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, catalog, _, _ = _project_services(config)
    if args.dry_run:
        location = resolved_project_location(args.path)
        display_name = _normalized(args.name, "name")
        duplicate = next(
            (
                item
                for item in repository.entries_for_migration()
                if item.harness_project_id
                == project_id_for_root(location.canonical_path or "")
            ),
            None,
        )
        if duplicate is not None:
            raise ProjectCatalogConflictError(
                "harness project identity is already cataloged"
            )
        payload: dict[str, Any] = {
            "operation": "add",
            "dry_run": True,
            "display_name": display_name,
            "harness_project_id": project_id_for_root(location.canonical_path or ""),
            "location": asdict(location),
        }
    else:
        payload = {
            "operation": "add",
            "dry_run": False,
            "project": asdict(catalog.add_project(args.path, display_name=args.name)),
        }
    return _render(
        args,
        payload,
        [f"{'Would add' if args.dry_run else 'Added'} project {args.name}"],
    )


@project_command_handler
def _handle_project_catalog_rename(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, catalog, _, _ = _project_services(config)
    current = repository.get(args.catalog_project_id)
    expected_revision = _expected_revision(current.revision, args.expected_revision)
    display_name = _normalized(args.new_name, "new_name")
    if args.dry_run:
        payload = {
            "operation": "rename",
            "dry_run": True,
            "catalog_project_id": current.catalog_project_id,
            "expected_revision": expected_revision,
            "current_name": current.display_name,
            "new_name": display_name,
        }
    else:
        payload = {
            "operation": "rename",
            "dry_run": False,
            "project": asdict(
                catalog.rename_project(
                    current.catalog_project_id,
                    display_name,
                    expected_revision=expected_revision,
                )
            ),
        }
    return _render(
        args,
        payload,
        [
            f"{'Would rename' if args.dry_run else 'Renamed'} project "
            f"{current.catalog_project_id} to {display_name}"
        ],
    )


@project_command_handler
def _handle_project_catalog_relocate(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, catalog, _, _ = _project_services(config)
    current = repository.get(args.catalog_project_id)
    expected_revision = _expected_revision(current.revision, args.expected_revision)
    preview = catalog.preview_relocation(
        current.catalog_project_id,
        args.new_path,
        expected_revision=expected_revision,
    )
    if args.dry_run:
        payload: dict[str, Any] = {
            "operation": "relocate",
            "dry_run": True,
            "preview": asdict(preview),
            "confirmation_required": not preview.identity_matches,
        }
    else:
        payload = {
            "operation": "relocate",
            "dry_run": False,
            "project": asdict(
                catalog.relocate_project(
                    preview,
                    preview_digest=preview.preview_digest,
                    allow_identity_change=args.confirm_identity_change,
                )
            ),
        }
    return _render(
        args,
        payload,
        [
            f"{'Would relocate' if args.dry_run else 'Relocated'} project "
            f"{current.catalog_project_id}"
        ],
    )


@project_command_handler
def _handle_project_catalog_remove(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, catalog, _, _ = _project_services(config)
    current = repository.get(args.catalog_project_id)
    expected_revision = _expected_revision(current.revision, args.expected_revision)
    if args.dry_run:
        payload: dict[str, Any] = {
            "operation": "remove",
            "dry_run": True,
            "catalog_project_id": current.catalog_project_id,
            "expected_revision": expected_revision,
            "repository_data_deleted": False,
            "session_data_deleted": False,
        }
    else:
        payload = {
            "operation": "remove",
            "dry_run": False,
            "project": asdict(
                catalog.remove_project(
                    current.catalog_project_id,
                    expected_revision=expected_revision,
                )
            ),
            "repository_data_deleted": False,
            "session_data_deleted": False,
        }
    return _render(
        args,
        payload,
        [
            f"{'Would tombstone' if args.dry_run else 'Tombstoned'} project "
            f"{current.catalog_project_id}; repository and sessions are preserved"
        ],
    )


@project_command_handler
def _handle_project_catalog_move_session(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    repository, _, _, _ = _project_services(config)
    session_store = FilesystemHarnessSessionStore(config.data_dir)
    current = session_store.get_session(args.session_id)
    expected_updated_at = (
        current.updated_at
        if args.expected_updated_at is None
        else args.expected_updated_at
    )
    if expected_updated_at != current.updated_at:
        raise ProjectCatalogConflictError("session revision is stale")
    target = None if args.target_project_id == "unfiled" else args.target_project_id
    if target is not None:
        project = repository.get(target)
        if project.state == "tombstoned":
            raise ProjectCatalogConflictError(
                "cannot move a session to a tombstoned project"
            )
    if args.dry_run:
        payload: dict[str, Any] = {
            "operation": "move_session",
            "dry_run": True,
            "session_id": current.id,
            "expected_updated_at": expected_updated_at,
            "from_catalog_project_id": current.metadata.get("catalog_project_id"),
            "to_catalog_project_id": target,
        }
    else:
        moved = SessionCatalogBindingService(repository, session_store).move_session(
            current.id,
            to_catalog_project_id=target,
            expected_updated_at=expected_updated_at,
        )
        payload = {
            "operation": "move_session",
            "dry_run": False,
            "session": {
                "id": moved.id,
                "updated_at": moved.updated_at,
                "catalog_project_id": moved.metadata.get("catalog_project_id"),
            },
        }
    label = target or "unfiled"
    return _render(
        args,
        payload,
        [
            f"{'Would move' if args.dry_run else 'Moved'} session {current.id} to {label}"
        ],
    )


@project_command_handler
def _handle_project_profile_list(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    _, _, repository, _ = _project_services(config)
    page = repository.list_page(
        args.catalog_project_id,
        cursor=args.cursor,
        limit=args.limit,
    )
    payload = {
        "launch_profiles": [asdict(item) for item in page.items],
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
    }
    lines = [
        f"{item['launch_profile_id']}  {item['display_name']}"
        for item in payload["launch_profiles"]
    ]
    return _render(args, payload, lines or ["No launch profiles"])


@project_command_handler
def _handle_project_profile_create(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    catalog_repository, _, _, profiles = _project_services(config)
    project = catalog_repository.get(args.catalog_project_id)
    if project.state == "tombstoned":
        raise ProjectCatalogConflictError(
            "cannot create a launch profile for a tombstoned project"
        )
    values = _profile_create_values(args)
    if args.dry_run:
        preview = _profile_preview(
            catalog_project_id=project.catalog_project_id,
            **values,
        )
        payload: dict[str, Any] = {
            "operation": "create_profile",
            "dry_run": True,
            "launch_profile": asdict(preview),
        }
    else:
        created = profiles.create_profile(project.catalog_project_id, **values)
        payload = {
            "operation": "create_profile",
            "dry_run": False,
            "launch_profile": asdict(created),
        }
    return _render(
        args,
        payload,
        [f"{'Would create' if args.dry_run else 'Created'} launch profile {args.name}"],
    )


@project_command_handler
def _handle_project_profile_update(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    _, _, repository, profiles = _project_services(config)
    current = repository.get(args.launch_profile_id)
    expected_revision = _expected_revision(current.revision, args.expected_revision)
    changes = _profile_update_values(args)
    if not changes:
        raise ValueError("profile update requires at least one changed field")
    if args.dry_run:
        preview = replace(
            current,
            **changes,
            revision=current.revision + 1,
            digest="0" * 64,
        )
        payload: dict[str, Any] = {
            "operation": "update_profile",
            "dry_run": True,
            "expected_revision": expected_revision,
            "launch_profile": asdict(preview),
        }
    else:
        updated = profiles.update_profile(
            current.launch_profile_id,
            expected_revision=expected_revision,
            **changes,
        )
        payload = {
            "operation": "update_profile",
            "dry_run": False,
            "launch_profile": asdict(updated),
        }
    return _render(
        args,
        payload,
        [
            f"{'Would update' if args.dry_run else 'Updated'} launch profile "
            f"{current.launch_profile_id}"
        ],
    )


@project_command_handler
def _handle_project_profile_delete(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    _, _, repository, profiles = _project_services(config)
    current = repository.get(args.launch_profile_id)
    expected_revision = _expected_revision(current.revision, args.expected_revision)
    deleted = (
        current
        if args.dry_run
        else profiles.delete_profile(
            current.launch_profile_id,
            expected_revision=expected_revision,
        )
    )
    payload = {
        "operation": "delete_profile",
        "dry_run": args.dry_run,
        "launch_profile": asdict(deleted),
    }
    return _render(
        args,
        payload,
        [
            f"{'Would delete' if args.dry_run else 'Deleted'} launch profile "
            f"{current.launch_profile_id}"
        ],
    )


def _project_services(config: HarnessConfig):
    root = Path(config.data_dir) / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(root / "catalog")
    profile_repository = FilesystemLaunchProfileRepository(root / "launch_profiles")
    return (
        catalog_repository,
        ProjectCatalogService(catalog_repository),
        profile_repository,
        ProjectLaunchProfileService(profile_repository, catalog_repository),
    )


def _profile_create_values(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "display_name": args.name,
        "agent_hint": args.agent_hint,
        "structured_route_hint": args.structured_route_hint,
        "model_hint": args.model_hint,
        "mode_hint": args.mode_hint,
        "host_hint": args.host_hint,
        "workspace_policy_hint": args.workspace_policy_hint,
        "terminal_mode_hint": args.terminal_mode_hint,
    }


def _profile_update_values(args: argparse.Namespace) -> dict[str, Any]:
    allowed = {
        "display_name",
        "agent_hint",
        "structured_route_hint",
        "model_hint",
        "mode_hint",
        "host_hint",
        "workspace_policy_hint",
        "terminal_mode_hint",
    }
    return {key: value for key, value in vars(args).items() if key in allowed}


def _profile_preview(
    *,
    catalog_project_id: str,
    display_name: str,
    agent_hint: str | None,
    structured_route_hint: str | None,
    model_hint: str | None,
    mode_hint: str | None,
    host_hint: str | None,
    workspace_policy_hint: str | None,
    terminal_mode_hint: str | None,
) -> ProjectLaunchProfileV1:
    return ProjectLaunchProfileV1(
        launch_profile_id=f"launch_{'0' * 24}",
        catalog_project_id=catalog_project_id,
        display_name=_normalized(display_name, "name"),
        agent_hint=_normalized_optional(agent_hint, "agent_hint"),
        structured_route_hint=_normalized_optional(
            structured_route_hint, "structured_route_hint"
        ),
        model_hint=_normalized_optional(model_hint, "model_hint"),
        mode_hint=_normalized_optional(mode_hint, "mode_hint"),
        host_hint=_normalized_optional(host_hint, "host_hint"),
        workspace_policy_hint=_normalized_optional(
            workspace_policy_hint, "workspace_policy_hint"
        ),
        terminal_mode_hint=terminal_mode_hint,  # type: ignore[arg-type]
        revision=1,
        digest="0" * 64,
    )


def _normalized(value: object, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _normalized_optional(value: str | None, field: str) -> str | None:
    return None if value is None else _normalized(value, field)


def _expected_revision(current: int, presented: int | None) -> int:
    expected = current if presented is None else presented
    if expected != current:
        raise ProjectCatalogConflictError("project catalog revision is stale")
    return expected


def _project_lines(projects: list[dict[str, Any]]) -> list[str]:
    return [
        f"{item['catalog_project_id']}  {item['state']:<10}  {item['display_name']}"
        for item in projects
    ] or ["No catalog projects"]


def _render(
    args: argparse.Namespace,
    payload: dict[str, Any],
    lines: list[str],
) -> int:
    if args.json:
        print_json(payload)
    else:
        for line in lines:
            print(line)
    return 0


__all__ = [
    "PROJECT_CATALOG_HANDLERS",
    "resolve_project_command_handler",
]
