"""Preparation phase for attachments and workspace execution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gigaloom.attachments import (
    render_attachments_for_harness,
    render_plan_to_dict,
)
from gigaloom.execution.attachments import PreparedAttachments
from gigaloom.execution.context import RunExecutionContext
from gigaloom.projects.api import (
    WorkspaceExecution,
    WorkspacePolicy,
    parse_workspace_policy,
    prepare_workspace_execution,
)


class RunPreparationService:
    """Prepare provider inputs without invoking a harness."""

    def previous_messages(
        self,
        store: Any,
        session_id: str,
        *,
        edit_message_id: str | None,
        current_user_message_id: str | None,
        limit: int,
    ) -> tuple[Any, ...]:
        """Read one bounded active conversation window for the current turn."""
        if current_user_message_id is not None:
            current = store.get_message(current_user_message_id)
            edited_from = _optional_text(current.metadata.get("edited_from_message_id"))
            if edit_message_id is not None and edited_from != edit_message_id:
                raise ValueError("Edited user message branch does not match its source")
            before = store.list_recent_messages(
                session_id,
                limit=limit,
                before=current_user_message_id,
            )
            tail = store.list_recent_messages(session_id, limit=limit)
            tail_ids = tuple(message.id for message in tail)
            try:
                after = tail[tail_ids.index(current_user_message_id) + 1 :]
            except ValueError:
                after = tail
            return (*before, *after)[-limit:]
        if edit_message_id is not None:
            edited = store.get_message(edit_message_id)
            if edited.role != "user":
                raise ValueError("Only the latest user message can be edited")
            tail = store.list_recent_messages(session_id, limit=limit)
            latest_user = next(
                (message for message in reversed(tail) if message.role == "user"),
                None,
            )
            if latest_user is None or latest_user.id != edit_message_id:
                raise ValueError("Only the latest user message can be edited")
            return store.list_recent_messages(
                session_id,
                limit=limit,
                before=edit_message_id,
            )
        return store.list_recent_messages(session_id, limit=limit)

    def prepare_attachments(
        self,
        runner: Any,
        context: RunExecutionContext,
        *,
        metadata_factory: Any,
    ) -> PreparedAttachments:
        """Load session-owned attachments and build the provider render plan."""
        session = context.session
        options = context.options
        attachments = runner._load_attachments(
            session.id,
            options["attachment_ids"],
        )
        metadata = tuple(metadata_factory(attachment) for attachment in attachments)
        render_plan = (
            render_attachments_for_harness(
                options["harness_id"],
                attachments,
                runner.attachment_store,
                prompt=options["prompt"],
            )
            if metadata
            else None
        )
        return PreparedAttachments(
            attachments=attachments,
            metadata=metadata,
            render_plan=render_plan,
            render_plan_payload=(
                render_plan_to_dict(render_plan) if render_plan is not None else None
            ),
        )

    def prepare_workspace(
        self,
        context: RunExecutionContext,
        *,
        run_id: str,
        data_dir: str,
    ) -> WorkspaceExecution:
        """Reuse a proven continuation worktree or prepare a new workspace."""
        continued = _continued_workspace_execution(
            context.session,
            context.options,
            data_dir=data_dir,
        )
        if continued is not None:
            return continued
        options = context.options
        return prepare_workspace_execution(
            requested_policy=options["workspace_policy"],
            harness_kind=options["harness_kind"],
            mode=options["mode"],
            workspace=options["workspace"],
            data_dir=data_dir,
            session_id=context.session.id,
            run_id=run_id,
            dry_run=bool(options["extra"].get("dry_run")),
        )


def _continued_workspace_execution(
    session: Any,
    options: Any,
    *,
    data_dir: str,
) -> WorkspaceExecution | None:
    """Reuse the first isolated edit worktree for later app-server turns."""
    link = _mapping(session.metadata.get("app_server_thread"))
    snapshot = _mapping(link.get("snapshot"))
    if not link or snapshot.get("permission_mode") != "edit":
        return None
    if options.get("harness_id") != "codex-cli" or options.get("mode") != "edit":
        return None
    source = _optional_text(snapshot.get("source_workspace"))
    effective = _optional_text(snapshot.get("workspace"))
    requested_source = _optional_text(options.get("workspace"))
    if source != requested_source or effective is None:
        raise ValueError(
            "Codex app-server edit continuation changed its source workspace; "
            "fork explicitly."
        )
    effective_path = Path(effective).expanduser().resolve()
    owned_root = Path(data_dir).expanduser().resolve() / "worktrees"
    try:
        effective_path.relative_to(owned_root)
    except ValueError as exc:
        raise ValueError(
            "Stored Codex app-server worktree is outside Harness ownership"
        ) from exc
    if not effective_path.is_dir():
        raise ValueError(
            "Stored Codex app-server worktree is unavailable; fork explicitly."
        )
    requested_policy = parse_workspace_policy(options.get("workspace_policy"))
    return WorkspaceExecution(
        requested_policy=requested_policy,
        policy=WorkspacePolicy.WORKTREE,
        source_workspace=source,
        source_git_root=_optional_text(snapshot.get("source_git_root")),
        effective_workspace=str(effective_path),
        worktree_path=str(effective_path),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
