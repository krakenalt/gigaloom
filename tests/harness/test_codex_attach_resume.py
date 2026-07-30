"""Live attach and truthful cold-resume contracts for native Codex."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from gigaloom.native.codex_operator import (
    CodexAttachResumeService,
    CodexBindingAccessError,
    CodexCapabilityState,
    CodexCwdDecision,
    CodexManagedLaunch,
    CodexManagedLaunchRequest,
    CodexResumeMode,
    CodexSessionBindingStore,
    codex_session_binding_to_dict,
    parse_codex_operator_args,
)
from gigaloom.native.terminal import (
    LocalTerminalAttachService,
    ManagedTerminalRegistry,
    TerminalAccess,
    TerminalIdentity,
    TerminalLiveness,
    TerminalLivenessKind,
    TerminalMetadataStore,
    TerminalState,
    digest_terminal_command,
    digest_terminal_path,
)


NOW = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)


class _Backend:
    def __init__(self, liveness: TerminalLiveness) -> None:
        self.observed = liveness
        self.attach_calls = []

    def liveness(self, terminal_id):
        del terminal_id
        return self.observed

    def attach_local(self, terminal_id):
        self.attach_calls.append(terminal_id)
        return 0


class _Launcher:
    def __init__(self, registry: ManagedTerminalRegistry) -> None:
        self.registry = registry
        self.requests = []

    def launch(self, request):
        self.requests.append(request)
        identity = _identity(
            request,
            native_session_id=request.native_session_id,
        )
        terminal = self.registry.ensure(
            identity,
            launch=lambda _: TerminalState.RUNNING,
        )
        return CodexManagedLaunch(
            terminal=terminal,
            compatibility_status=CodexCapabilityState.SUPPORTED,
            private_home_digest="a" * 64,
            app_server_command_digest="b" * 64,
        )


def _request(
    workspace: Path,
    access: TerminalAccess,
    *,
    native_thread_id: str | None = "thread-fixture",
    session_key: str = "primary",
) -> CodexManagedLaunchRequest:
    return CodexManagedLaunchRequest(
        access=access,
        terminal_name="codex",
        session_key=session_key,
        command=("/fixture/codex",),
        executable_version="0.144.5",
        cwd=workspace,
        native_session_id=native_thread_id,
    )


def _identity(request, *, native_session_id):
    command = (
        *request.command,
        "--remote",
        "unix:///fixture",
        "--strict-config",
        "--cd",
        str(request.cwd),
        *request.tui_args,
    )
    return TerminalIdentity(
        owner_id=request.access.owner_id,
        workspace_id=request.access.workspace_id,
        session_id=request.access.session_id,
        terminal_name=request.terminal_name,
        session_key=request.session_key,
        native_harness_id="codex-cli",
        command_digest=digest_terminal_command(command),
        cwd_digest=digest_terminal_path(request.cwd),
        executable_path_digest=digest_terminal_path(request.command[0]),
        executable_version=request.executable_version,
        native_session_id=native_session_id,
    )


def _service(
    tmp_path,
    registry,
    backend,
    launcher,
):
    return CodexAttachResumeService(
        CodexSessionBindingStore(tmp_path / "state"),
        registry,
        backend,
        LocalTerminalAttachService(
            registry,
            TerminalMetadataStore(tmp_path / "state"),
            backend,
            now=lambda: NOW,
        ),
        launcher,
        now=lambda: NOW,
    )


def _initial_launch(registry, request):
    terminal = registry.ensure(
        _identity(request, native_session_id=request.native_session_id),
        launch=lambda _: TerminalState.RUNNING,
    )
    return CodexManagedLaunch(
        terminal=terminal,
        compatibility_status=CodexCapabilityState.SUPPORTED,
        private_home_digest="a" * 64,
        app_server_command_digest="b" * 64,
    )


def test_live_terminal_reattaches_without_launching_new_thread(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    access = TerminalAccess("owner", "workspace", "session")
    registry = ManagedTerminalRegistry(
        now=lambda: NOW,
        id_factory=lambda: "term-live",
    )
    request = _request(workspace, access)
    launched = _initial_launch(registry, request)
    backend = _Backend(TerminalLiveness(TerminalLivenessKind.LIVE))
    launcher = _Launcher(registry)
    service = _service(tmp_path, registry, backend, launcher)
    binding = service.bind(
        launched,
        request,
        web_base_url="http://127.0.0.1:8420",
    )

    outcome = service.resume(
        binding.id,
        access,
        request,
        web_base_url="http://127.0.0.1:8420",
    )

    assert outcome.mode is CodexResumeMode.LIVE_REATTACH
    assert outcome.message == "Detached; Codex remains live."
    assert outcome.web_url.endswith("/operator/terminals/term-live")
    assert outcome.resume_hint == ("giga", "codex", "--resume", binding.id)
    assert backend.attach_calls == ["term-live"]
    assert launcher.requests == []


def test_dead_terminal_cold_resumes_exact_native_thread(tmp_path):
    bound = tmp_path / "bound"
    bound.mkdir()
    access = TerminalAccess("owner", "workspace", "session")
    ids = iter(("term-old", "term-new"))
    registry = ManagedTerminalRegistry(
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    request = _request(bound, access)
    launched = _initial_launch(registry, request)
    registry.observe_liveness(
        launched.terminal.id,
        access,
        TerminalState.EXITED,
        expected_revision=launched.terminal.revision,
    )
    backend = _Backend(TerminalLiveness(TerminalLivenessKind.EXITED))
    launcher = _Launcher(registry)
    service = _service(tmp_path, registry, backend, launcher)
    binding = service.bind(
        replace(
            launched,
            terminal=registry.get(launched.terminal.id, access),
        ),
        request,
        web_base_url="http://localhost:8420",
    )

    outcome = service.resume(
        binding.id,
        access,
        request,
        web_base_url="http://localhost:8420",
    )

    assert outcome.mode is CodexResumeMode.COLD_RESUME
    assert outcome.terminal is not None
    assert outcome.terminal.id == "term-new"
    assert launcher.requests[0].tui_args == ("resume", "thread-fixture")
    assert launcher.requests[0].native_session_id == "thread-fixture"
    assert launcher.requests[0].session_key.startswith("resume-")


def test_cwd_mismatch_requires_explicit_choice(tmp_path):
    bound = tmp_path / "bound"
    current = tmp_path / "current"
    bound.mkdir()
    current.mkdir()
    access = TerminalAccess("owner", "workspace", "session")
    ids = iter(("term-old", "term-new"))
    registry = ManagedTerminalRegistry(
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    bound_request = _request(bound, access)
    launched = _initial_launch(registry, bound_request)
    registry.observe_liveness(
        launched.terminal.id,
        access,
        TerminalState.EXITED,
        expected_revision=launched.terminal.revision,
    )
    backend = _Backend(TerminalLiveness(TerminalLivenessKind.EXITED))
    launcher = _Launcher(registry)
    service = _service(tmp_path, registry, backend, launcher)
    binding = service.bind(
        replace(
            launched,
            terminal=registry.get(launched.terminal.id, access),
        ),
        bound_request,
        web_base_url="https://localhost",
    )
    current_request = _request(current, access)

    blocked = service.resume(
        binding.id,
        access,
        current_request,
        web_base_url="https://localhost",
    )
    resumed = service.resume(
        binding.id,
        access,
        current_request,
        cwd_decision=CodexCwdDecision.CURRENT,
        web_base_url="https://localhost",
    )

    assert blocked.mode is CodexResumeMode.BLOCKED
    assert blocked.reason_code == "cwd_decision_required"
    assert launcher.requests[0].cwd == current.resolve()
    assert resumed.mode is CodexResumeMode.COLD_RESUME


def test_missing_native_thread_requires_explicit_fresh(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    access = TerminalAccess("owner", "workspace", "session")
    ids = iter(("term-old", "term-new"))
    registry = ManagedTerminalRegistry(
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    request = _request(workspace, access, native_thread_id=None)
    launched = _initial_launch(registry, request)
    registry.observe_liveness(
        launched.terminal.id,
        access,
        TerminalState.EXITED,
        expected_revision=launched.terminal.revision,
    )
    backend = _Backend(TerminalLiveness(TerminalLivenessKind.EXITED))
    launcher = _Launcher(registry)
    service = _service(tmp_path, registry, backend, launcher)
    binding = service.bind(
        replace(
            launched,
            terminal=registry.get(launched.terminal.id, access),
        ),
        request,
        web_base_url="http://localhost",
    )

    blocked = service.resume(
        binding.id,
        access,
        request,
        web_base_url="http://localhost",
    )
    fresh = service.resume(
        binding.id,
        access,
        request,
        fresh=True,
        web_base_url="http://localhost",
    )

    assert blocked.reason_code == "native_thread_id_required"
    assert fresh.mode is CodexResumeMode.FRESH
    assert launcher.requests[0].tui_args == ()
    assert launcher.requests[0].native_session_id is None


def test_binding_store_is_private_and_fails_closed_across_owner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    access = TerminalAccess("owner", "workspace", "session")
    registry = ManagedTerminalRegistry(
        now=lambda: NOW,
        id_factory=lambda: "term-private",
    )
    request = _request(workspace, access)
    launched = _initial_launch(registry, request)
    backend = _Backend(TerminalLiveness(TerminalLivenessKind.LIVE))
    service = _service(tmp_path, registry, backend, _Launcher(registry))
    binding = service.bind(
        launched,
        request,
        web_base_url="http://localhost",
    )

    stored = next(service.store.root.glob("*.json"))
    assert stored.stat().st_mode & 0o777 == 0o600
    public = codex_session_binding_to_dict(binding)
    assert binding.native_thread_id not in repr(public)
    assert binding.cwd not in repr(public)
    assert public["has_native_thread_id"] is True
    with pytest.raises(CodexBindingAccessError):
        service.store.load(
            binding.id,
            TerminalAccess("other", "workspace", "session"),
        )
    payload = json.loads(stored.read_text(encoding="utf-8"))
    payload["generation"] = True
    stored.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="generation"):
        service.store.load(binding.id, access)


def test_lightweight_command_parser_preserves_provider_suffix():
    start = parse_codex_operator_args(("--model", "gpt-5.4"))
    resumed = parse_codex_operator_args(
        (
            "--resume",
            "codex_fixture",
            "--use-current-cwd",
            "--",
            "--model",
            "gpt-5.4",
        )
    )

    assert start.provider_args == ("--model", "gpt-5.4")
    assert start.resume_binding_id is None
    assert resumed.resume_binding_id == "codex_fixture"
    assert resumed.cwd_decision is CodexCwdDecision.CURRENT
    assert resumed.provider_args == ("--model", "gpt-5.4")
    with pytest.raises(ValueError, match="cwd decision"):
        parse_codex_operator_args(
            (
                "--resume",
                "codex_fixture",
                "--use-bound-cwd",
                "--use-current-cwd",
            )
        )
