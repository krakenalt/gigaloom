"""Admission phase for one Harness session run."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from gpt2giga_harness.execution.context import RunExecutionContext


class RunAdmissionService:
    """Resolve immutable run identity before preparation side effects."""

    def admit(
        self,
        runner: Any,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        user_message_id: str | None,
        excluded_history_run_ids: tuple[str, ...],
        new_message_id: Callable[[str], str],
        history_resolver: Callable[..., tuple[Any, ...]],
        edit_message_id: Callable[[Mapping[str, Any]], str | None],
    ) -> RunExecutionContext:
        """Resolve session, options, harness, and active conversation history."""
        session = runner.store.get_session(session_id)
        options = runner._run_options(payload, session=session)
        session, provider_account_binding = runner._prepare_provider_account_session(
            session,
            options,
        )
        harness = runner.registry.get(options["harness_id"])
        logical_user_message_id = user_message_id or new_message_id("msg")
        previous_messages = ()
        if not bool(_mapping(options["extra"]).get("isolated_history")):
            previous_messages = tuple(
                message
                for message in history_resolver(
                    runner.store.list_messages(session.id),
                    edit_message_id=edit_message_id(options),
                    current_user_message_id=user_message_id,
                )
                if message.run_id not in excluded_history_run_ids
            )
        return RunExecutionContext(
            session=session,
            options=options,
            harness=harness,
            logical_user_message_id=logical_user_message_id,
            previous_messages=previous_messages,
            provider_account_binding=provider_account_binding,
        )

    def validate_continuation_identity(
        self,
        context: RunExecutionContext,
    ) -> None:
        """Reject incompatible structured continuation before run side effects."""
        session = context.session
        options = context.options
        if session.metadata.get("app_server_fork"):
            return
        link = _mapping(session.metadata.get("app_server_thread"))
        snapshot = _mapping(link.get("snapshot"))
        if not link or options.get("harness_id") != "codex-cli":
            return
        extra = _mapping(options.get("extra"))
        managed_mcp = _mapping(extra.get("managed_mcp_snapshot"))
        actual = {
            "harness_id": options.get("harness_id"),
            "api_mode": getattr(
                options.get("api_mode"),
                "value",
                options.get("api_mode"),
            ),
            "model": options.get("model"),
            "source_workspace": options.get("workspace"),
            "permission_mode": options.get("mode"),
            "tool_snapshot_hash": managed_mcp.get("snapshot_hash"),
        }
        mismatched = [
            key for key, value in actual.items() if snapshot.get(key) != value
        ]
        if mismatched:
            raise ValueError(
                "Codex app-server continuation changed "
                + ", ".join(mismatched)
                + "; fork explicitly."
            )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
