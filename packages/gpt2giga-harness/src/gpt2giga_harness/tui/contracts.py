"""Stable typed contracts shared by TUI transport adapters."""

from __future__ import annotations

from dataclasses import dataclass


MAX_PROJECTS = 50
MAX_SESSIONS = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DISPLAY_CHARS = 512
MAX_TIMELINE_EVENTS = 100
MAX_TIMELINE_CHARS = 64 * 1024
MAX_FILE_CANDIDATES = 20
MAX_FILE_PREVIEW_CHARS = 8 * 1024
MAX_DIFF_PREVIEW_CHARS = 32 * 1024
MAX_NATIVE_SCROLLBACK_CHARS = 64 * 1024
MAX_NATIVE_INPUT_CHARS = 8 * 1024
HTTP_TIMEOUT_SECONDS = 10.0
RUN_START_TIMEOUT_SECONDS = 5.0
RUN_STREAM_RESNAPSHOT_SECONDS = 15.0
MAX_SSE_LINE_BYTES = 256 * 1024
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "canceled"})
_TERMINAL_PROCESS_STATUSES = frozenset(
    {"exited", "stopped", "failed", "timed_out", "interrupted", "unknown"}
)


class WorkbenchClientError(RuntimeError):
    """Bounded client failure safe for presentation."""


@dataclass(frozen=True)
class ProjectSummary:
    """Bounded project navigation item."""

    id: str
    name: str
    root: str
    git_branch: str | None
    session_count: int


@dataclass(frozen=True)
class SessionSummary:
    """Bounded session navigation item."""

    id: str
    title: str
    updated_at: str
    workspace: str | None
    harness_id: str
    model: str | None
    mode: str
    archived: bool = False
    project_id: str | None = None
    preview: str = ""
    native_authority: str | None = None
    native_session_id: str | None = None
    native_operation: str | None = None
    revision: str = ""
    generation: int = 0
    lease: str | None = None
    task_intent: str = "ask"
    authority: str = "read_only"
    compatibility_warning: str | None = None


@dataclass(frozen=True)
class SessionActionBinding:
    """Exact session state presented before a navigation mutation."""

    session_id: str
    revision: str
    generation: int
    lease: str | None
    idempotency_key: str


@dataclass(frozen=True)
class SessionPreview:
    """Bounded session preview and transcript-search result."""

    session: SessionSummary
    transcript: tuple[str, ...]
    match_count: int
    truncated: bool


@dataclass(frozen=True)
class SessionExport:
    """Sanitized local export created by session application authority."""

    session_id: str
    path: str
    message_count: int


@dataclass(frozen=True)
class HarnessSummary:
    """Content-free Harness availability projection."""

    id: str
    title: str
    availability: str
    reason: str
    default_transport: str


@dataclass(frozen=True)
class ReadinessSummary:
    """Selected provider/Harness/model/transport readiness."""

    status: str
    provider: str
    provider_status: str
    harness_id: str
    harness_status: str
    model: str | None
    transport: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationSummary:
    """Content-free durable integration inventory status."""

    status: str
    catalog_count: int
    flow_count: int
    verified_count: int


@dataclass(frozen=True)
class EnvironmentSummary:
    """Bounded presentation of one canonical local Git environment."""

    status: str
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    worktree_root: str | None = None
    staged_count: int = 0
    unstaged_count: int = 0
    untracked_count: int = 0
    additions: int = 0
    deletions: int = 0
    commit_ready: bool = False
    push_ready: bool = False
    push_blocker: str | None = None
    captured_at: str | None = None
    issue_pr_status: str = "not_connected"
    github_status: str = "unavailable"
    github_repository: str | None = None
    github_checks: str = "unavailable"
    github_actions: str = "unavailable"
    github_run_count: int = 0
    github_checked_at: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EnvironmentCommitPreviewSummary:
    """Exact author/message and immutable Git state shown before approval."""

    id: str
    branch: str
    head: str | None
    diff_sha256: str
    staged_count: int
    message: str
    author_name: str
    author_email: str
    worktree_root: str


@dataclass(frozen=True)
class EnvironmentCommitApplySummary:
    """Governed commit outcome for either approval or exact completion."""

    preview: EnvironmentCommitPreviewSummary
    approval: ApprovalSummary | None = None
    commit_head: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True)
class EnvironmentPushPreviewSummary:
    """Exact local/remote Git state shown before remote-write approval."""

    id: str
    branch: str
    head: str
    diff_sha256: str
    remote: str
    upstream: str | None
    target_branch: str
    remote_head: str | None
    repository: str
    worktree_root: str


@dataclass(frozen=True)
class EnvironmentPushApplySummary:
    """Governed push outcome for either approval or exact completion."""

    preview: EnvironmentPushPreviewSummary
    approval: ApprovalSummary | None = None
    commit_head: str | None = None
    remote_commit_url: str | None = None
    run_evidence_url: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True)
class EnvironmentPullRequestPreviewSummary:
    """Exact hosted PR content and immutable source/base state."""

    id: str
    repository: str
    remote: str
    source_branch: str
    source_head: str
    source_remote_head: str
    base_branch: str
    base_head: str
    diff_sha256: str
    title: str
    body: str
    worktree_root: str


@dataclass(frozen=True)
class EnvironmentPullRequestApplySummary:
    """Governed PR outcome for approval or exact hosted completion."""

    preview: EnvironmentPullRequestPreviewSummary
    approval: ApprovalSummary | None = None
    number: int | None = None
    commit_head: str | None = None
    pull_request_url: str | None = None
    commit_url: str | None = None
    checks_url: str | None = None
    run_evidence_url: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True)
class NavigationSnapshot:
    """One authoritative, presentation-bounded TUI resnapshot."""

    transport_mode: str
    projects: tuple[ProjectSummary, ...]
    project: ProjectSummary
    sessions: tuple[SessionSummary, ...]
    selected_session_id: str | None
    harnesses: tuple[HarnessSummary, ...]
    readiness: ReadinessSummary
    integrations: IntegrationSummary = IntegrationSummary("unknown", 0, 0, 0)
    environment: EnvironmentSummary = EnvironmentSummary("unavailable")


@dataclass(frozen=True)
class RunActionBinding:
    """Exact mutable run identity presented before one user action."""

    session_id: str
    run_id: str
    revision: str
    generation: int
    idempotency_key: str


@dataclass(frozen=True)
class TimelineEvent:
    """Bounded normalized event suitable for terminal rendering."""

    id: str
    type: str
    message: str
    delta: str | None = None
    tool_name: str | None = None
    approval_id: str | None = None
    input_id: str | None = None
    category: str = "status"
    stream: str | None = None
    artifact_id: str | None = None
    artifact_kind: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ApprovalSummary:
    """Redaction-safe pending approval projected into the selected run."""

    id: str
    action: str
    reason: str
    status: str
    enforcement: str = "unknown"
    enforcement_owner: str = "unknown"
    policy_source: str = "unknown"
    executable: str = "not declared"
    tool: str = "not declared"
    cwd: str = "not declared"
    paths: tuple[str, ...] = ()
    network: str = "not declared"
    mutation_class: str = "unknown"
    decision_scopes: tuple[str, ...] = ("allow_once", "deny")
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunSnapshot:
    """Bounded authoritative run state shared by in-process and attach modes."""

    binding: RunActionBinding
    status: str
    events: tuple[TimelineEvent, ...]
    cursor: str | None
    pending_approvals: tuple[ApprovalSummary, ...] = ()
    resnapshot_reason: str | None = None
    execution_transport: str | None = None
    native_process_id: str | None = None

    @property
    def terminal(self) -> bool:
        """Return whether the durable run reached a terminal state."""
        return self.status in _TERMINAL_RUN_STATUSES


@dataclass(frozen=True)
class FileCandidate:
    """Safe bounded project file candidate and preview."""

    path: str
    name: str
    mime_type: str
    kind: str
    size_bytes: int
    preview: str
    preview_status: str


@dataclass(frozen=True)
class AttachmentSummary:
    """One backend-owned attachment selected for the next turn."""

    id: str
    path: str
    mime_type: str
    kind: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactSummary:
    """Content-free retained artifact inventory item."""

    type: str
    byte_count: int | None


@dataclass(frozen=True)
class RunInspection:
    """Bounded authoritative diff, evidence, and recovery projection."""

    run_id: str
    status: str
    revision: str
    provider_continuity: str
    harness_status: str
    recovery: str
    artifacts: tuple[ArtifactSummary, ...]
    diff: str
    diff_truncated: bool
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class HandoffPreview:
    """Exact external handoff boundary shown before leaving the TUI."""

    kind: str
    status: str
    target: str
    continuity: str
    observability: tuple[str, ...]
    instruction: str
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeTerminalSnapshot:
    """Bounded, terminal-neutral native-process projection."""

    process_id: str
    session_id: str
    run_id: str
    harness_id: str
    transport: str
    status: str
    cursor: int
    output: str = ""
    output_truncated: bool = False
    exit_code: int | None = None
    handoff_required: bool = False

    @property
    def terminal(self) -> bool:
        """Return whether the native process reached a terminal state."""
        return self.status in _TERMINAL_PROCESS_STATUSES
