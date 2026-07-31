import type { ApprovalRequest } from "./approvals";

export interface EnvironmentResponse {
  environment: {
    schema_version: number;
    provider_id: string;
    repository_root: string;
    worktree_root: string;
    branch: string | null;
    detached: boolean;
    head: string | null;
    base_identity: string | null;
    upstream: string | null;
    ahead: number;
    behind: number;
    remote: string | null;
    staged_count: number;
    unstaged_count: number;
    untracked_count: number;
    additions: number;
    deletions: number;
    changed_paths: string[];
    changed_paths_truncated: boolean;
    diff_sha256: string;
    captured_at: string;
    push_ready: boolean;
    push_blocker: string | null;
  };
  commit: { blocker: string | null; ready: boolean };
  github: {
    schema_version: number;
    status: string;
    auth_status: string;
    checked_at: string;
    repository: {
      host: string;
      name_with_owner: string;
      url: string;
      default_branch: string | null;
      is_fork: boolean;
    } | null;
    pull_request: {
      number: number;
      state: string;
      url: string;
      draft: boolean;
      head_branch: string;
      base_branch: string;
      checks: GitHubCountRollup;
      issues: { number: number; state: string; url: string }[];
    } | null;
    runs: {
      database_id: number;
      status: string;
      conclusion: string | null;
      url: string;
      head_sha: string;
      created_at: string;
      updated_at: string;
      jobs: GitHubCountRollup;
    }[];
    reason_code: string | null;
    cached: boolean;
    stale: boolean;
  };
  issue_pr: {
    status: string;
    kind?: string;
    number?: number;
    url?: string;
    checks?: GitHubCountRollup;
    issues?: { number: number; state: string; url: string }[];
  };
  freshness: { captured_at: string; status: string };
}

export interface EnvironmentCommitPreview {
  id: string;
  branch: string;
  head: string | null;
  diff_sha256: string;
  staged_count: number;
  message: string;
  author: { name: string; email: string };
  worktree_root: string;
}

export interface EnvironmentCommitPreviewResponse {
  preview: EnvironmentCommitPreview;
}

export interface EnvironmentCommitApplyResponse {
  approval_required?: boolean;
  approval?: ApprovalRequest;
  preview: EnvironmentCommitPreview;
  result?: {
    preview_id: string;
    commit_head: string;
    parent_head: string | null;
    completed_at: string;
    recovered: boolean;
  };
  idempotent_replay?: boolean;
}

export interface EnvironmentPushPreview {
  id: string;
  repository: { host: string; name_with_owner: string };
  branch: string;
  head: string;
  diff_sha256: string;
  remote: string;
  upstream: string | null;
  target_branch: string;
  remote_ref: string;
  remote_head: string | null;
  permissions: {
    network_connect: boolean;
    remote_write: boolean;
    create_remote_branch: boolean;
    set_upstream: boolean;
    force_update: boolean;
    delete_remote_branch: boolean;
    follow_tags: boolean;
    execute_hooks: boolean;
  };
  worktree_root: string;
}

export interface EnvironmentPushPreviewResponse {
  preview: EnvironmentPushPreview;
}

export interface EnvironmentPushApplyResponse {
  approval_required?: boolean;
  approval?: ApprovalRequest;
  preview: EnvironmentPushPreview;
  result?: {
    preview_id: string;
    commit_head: string;
    remote: string;
    target_branch: string;
    remote_commit_url: string;
    run_evidence_url: string;
    upstream_configured: boolean;
    completed_at: string;
    recovered: boolean;
  };
  idempotent_replay?: boolean;
}

export interface EnvironmentPullRequestPreview {
  id: string;
  repository: { host: string; name_with_owner: string; url: string };
  remote: string;
  source_branch: string;
  source_head: string;
  source_remote_head: string;
  base_branch: string;
  base_head: string;
  diff_sha256: string;
  title: string;
  body: string;
  worktree_root: string;
}

export interface EnvironmentPullRequestPreviewResponse {
  preview: EnvironmentPullRequestPreview;
}

export interface EnvironmentPullRequestApplyResponse {
  approval_required?: boolean;
  approval?: ApprovalRequest;
  preview: EnvironmentPullRequestPreview;
  result?: {
    preview_id: string;
    number: number;
    state: string;
    source_branch: string;
    base_branch: string;
    commit_head: string;
    pull_request_url: string;
    commit_url: string;
    checks_url: string;
    run_evidence_url: string;
    completed_at: string;
    recovered: boolean;
  };
  idempotent_replay?: boolean;
}

export interface GitHubCountRollup {
  status: string;
  total: number;
  passed: number;
  failed: number;
  pending: number;
  skipped: number;
  cancelled: number;
  unknown: number;
}

export interface WorkspaceFileCandidate {
  path: string;
  name: string;
  kind: string;
  mime_type?: string | null;
  size_bytes: number;
}

export interface WorkspaceFileSearchResponse {
  q: string;
  files: WorkspaceFileCandidate[];
  bounded: true;
}

export interface ArenaWorkspaceFileSearchResponse {
  workspace: string;
  q: string;
  files: WorkspaceFileCandidate[];
}
