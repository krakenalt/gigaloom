"""Live attach and truthful cold resume orchestration for native Codex."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, urlsplit

from gigaloom.native.codex_operator.bindings import CodexSessionBindingStore
from gigaloom.native.codex_operator.launch import (
    CodexManagedLaunch,
    CodexManagedLaunchRequest,
)
from gigaloom.native.codex_operator.session_contracts import (
    CodexCwdDecision,
    CodexResumeMode,
    CodexResumeOutcome,
    CodexSessionBinding,
)
from gigaloom.native.terminal import (
    LocalTerminalAttachService,
    ManagedTerminalRegistry,
    TerminalAccess,
    TerminalLivenessKind,
    digest_terminal_path,
)


class _Launcher(Protocol):
    def launch(self, request: CodexManagedLaunchRequest) -> CodexManagedLaunch: ...


class _LivenessBackend(Protocol):
    def liveness(self, terminal_id: str): ...


class CodexAttachResumeService:
    """Prefer live reattach, then exact cold resume, never transcript replay."""

    def __init__(
        self,
        store: CodexSessionBindingStore,
        registry: ManagedTerminalRegistry,
        backend: _LivenessBackend,
        attach_service: LocalTerminalAttachService,
        launcher: _Launcher,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.backend = backend
        self.attach_service = attach_service
        self.launcher = launcher
        self._now = now or (lambda: datetime.now(timezone.utc))

    def bind(
        self,
        launched: CodexManagedLaunch,
        request: CodexManagedLaunchRequest,
        *,
        web_base_url: str,
    ) -> CodexSessionBinding:
        """Persist one exact private terminal/thread/workspace binding."""
        if launched.terminal.identity.access != request.access:
            raise ValueError("Codex launch binding access changed")
        now = self._timestamp()
        binding_id = _binding_id(request.access, launched.terminal.id)
        binding = CodexSessionBinding(
            id=binding_id,
            access=request.access,
            terminal_id=launched.terminal.id,
            terminal_revision=launched.terminal.revision,
            cwd=str(request.cwd),
            cwd_digest=digest_terminal_path(request.cwd),
            web_url=_web_url(web_base_url, launched.terminal.id),
            generation=1,
            created_at=now,
            updated_at=now,
            native_thread_id=request.native_session_id,
        )
        self.store.save(binding)
        return binding

    def resume(
        self,
        binding_id: str,
        access: TerminalAccess,
        launch_request: CodexManagedLaunchRequest,
        *,
        cwd_decision: CodexCwdDecision | None = None,
        fresh: bool = False,
        web_base_url: str,
    ) -> CodexResumeOutcome:
        """Reattach live state or cold-launch only with exact resume evidence."""
        binding = self.store.load(binding_id, access)
        if launch_request.access != access:
            raise ValueError("Codex resume launch access changed")
        current = self.registry.get(binding.terminal_id, access)
        observed = self.backend.liveness(current.id)
        if observed.kind is TerminalLivenessKind.LIVE:
            terminal, receipt = self.attach_service.attach(
                current.id,
                access,
                expected_revision=current.revision,
            )
            updated = replace(
                binding,
                terminal_revision=terminal.revision,
                updated_at=self._timestamp(),
            )
            self.store.save(updated)
            message = (
                "Detached; Codex remains live."
                if receipt.outcome.value == "detached"
                else "Codex terminal exited."
                if receipt.outcome.value == "exited"
                else "Codex attach failed."
            )
            return self._outcome(
                CodexResumeMode.LIVE_REATTACH,
                updated,
                terminal=terminal,
                message=message,
                reason_code=receipt.reason,
            )

        chosen_cwd = self._cold_cwd(binding, launch_request.cwd, cwd_decision)
        if chosen_cwd is None:
            return self._outcome(
                CodexResumeMode.BLOCKED,
                binding,
                terminal=None,
                message="Workspace changed; choose bound or current cwd explicitly.",
                reason_code="cwd_decision_required",
            )
        if binding.native_thread_id is None and not fresh:
            return self._outcome(
                CodexResumeMode.BLOCKED,
                binding,
                terminal=None,
                message="Cold resume requires an exact native Codex thread id.",
                reason_code="native_thread_id_required",
            )
        generation = binding.generation + 1
        cold_request = replace(
            launch_request,
            cwd=chosen_cwd,
            session_key=_resume_session_key(binding.id, generation),
            native_session_id=None if fresh else binding.native_thread_id,
            tui_args=(
                launch_request.tui_args
                if fresh
                else ("resume", binding.native_thread_id or "")
            ),
        )
        launched = self.launcher.launch(cold_request)
        updated = replace(
            binding,
            terminal_id=launched.terminal.id,
            terminal_revision=launched.terminal.revision,
            cwd=str(chosen_cwd),
            cwd_digest=digest_terminal_path(chosen_cwd),
            web_url=_web_url(web_base_url, launched.terminal.id),
            generation=generation,
            updated_at=self._timestamp(),
            native_thread_id=(None if fresh else binding.native_thread_id),
        )
        self.store.save(updated)
        return self._outcome(
            CodexResumeMode.FRESH if fresh else CodexResumeMode.COLD_RESUME,
            updated,
            terminal=launched.terminal,
            message=(
                "Started a fresh native Codex session."
                if fresh
                else "Cold-resumed the exact native Codex thread."
            ),
            reason_code=(
                "explicit_fresh_launch" if fresh else "exact_native_thread_resumed"
            ),
        )

    def _cold_cwd(
        self,
        binding: CodexSessionBinding,
        current_cwd: Path,
        decision: CodexCwdDecision | None,
    ) -> Path | None:
        current = Path(current_cwd).expanduser().resolve()
        if digest_terminal_path(current) == binding.cwd_digest:
            return current
        if decision is CodexCwdDecision.CURRENT:
            return current
        if decision is CodexCwdDecision.BOUND:
            bound = Path(binding.cwd).expanduser().resolve()
            if not bound.is_dir():
                raise ValueError("bound Codex workspace no longer exists")
            return bound
        return None

    def _outcome(
        self,
        mode: CodexResumeMode,
        binding: CodexSessionBinding,
        *,
        terminal,
        message: str,
        reason_code: str,
    ) -> CodexResumeOutcome:
        return CodexResumeOutcome(
            mode=mode,
            binding_id=binding.id,
            terminal=terminal,
            web_url=binding.web_url,
            resume_hint=("giga", "codex", "--resume", binding.id),
            message=message,
            reason_code=reason_code,
        )

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Codex resume clock must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()


def _binding_id(access: TerminalAccess, terminal_id: str) -> str:
    value = "\0".join(
        (
            access.owner_id,
            access.workspace_id,
            access.session_id,
            terminal_id,
        )
    )
    return f"codex_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _resume_session_key(binding_id: str, generation: int) -> str:
    value = f"{binding_id}:{generation}"
    return f"resume-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _web_url(base_url: str, terminal_id: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Codex Web base URL is invalid")
    base = base_url.rstrip("/")
    return f"{base}/operator/terminals/{quote(terminal_id, safe='')}"
