import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type EnvironmentCommitApplyResponse,
  type EnvironmentCommitPreview,
  type EnvironmentCommitPreviewResponse,
  type EnvironmentPullRequestApplyResponse,
  type EnvironmentPullRequestPreview,
  type EnvironmentPullRequestPreviewResponse,
  type EnvironmentPushApplyResponse,
  type EnvironmentPushPreview,
  type EnvironmentPushPreviewResponse,
  mutateCockpit,
} from "../../api";
import type { EnvironmentView } from "../../environment-model";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { requestKeys } from "../../request-graph";
import { formatTimestamp } from "../../surface-model";

type EnvironmentCommitDraft = {
  authorEmail: string;
  authorName: string;
  message: string;
};

type EnvironmentCommitAction = {
  draft: EnvironmentCommitDraft;
  error: boolean;
  notice: string | null;
  pending: boolean;
  preview: EnvironmentCommitPreview | undefined;
  setField: (field: keyof EnvironmentCommitDraft, value: string) => void;
  submit: () => void;
};

type EnvironmentPushAction = {
  error: boolean;
  notice: string | null;
  pending: boolean;
  preview: EnvironmentPushPreview | undefined;
  result: EnvironmentPushApplyResponse["result"] | undefined;
  submit: () => void;
};

type EnvironmentPullRequestDraft = {
  baseBranch: string;
  body: string;
  title: string;
};

type EnvironmentPullRequestAction = {
  draft: EnvironmentPullRequestDraft;
  error: boolean;
  notice: string | null;
  pending: boolean;
  preview: EnvironmentPullRequestPreview | undefined;
  result: EnvironmentPullRequestApplyResponse["result"] | undefined;
  setField: (field: keyof EnvironmentPullRequestDraft, value: string) => void;
  submit: () => void;
};

export function useEnvironmentActions(
  sessionId: string | undefined,
  locale: LocalePreference,
): {
  commitAction: EnvironmentCommitAction;
  pullRequestAction: EnvironmentPullRequestAction;
  pushAction: EnvironmentPushAction;
} {
  const queryClient = useQueryClient();
  const [commitDraft, setCommitDraft] = useState<EnvironmentCommitDraft>({
    authorEmail: "",
    authorName: "",
    message: "",
  });
  const [commitPreview, setCommitPreview] = useState<EnvironmentCommitPreview>();
  const [commitNotice, setCommitNotice] = useState<string | null>(null);
  const [pushPreview, setPushPreview] = useState<EnvironmentPushPreview>();
  const [pushResult, setPushResult] =
    useState<EnvironmentPushApplyResponse["result"]>();
  const [pushNotice, setPushNotice] = useState<string | null>(null);
  const [pullRequestDraft, setPullRequestDraft] =
    useState<EnvironmentPullRequestDraft>({
      baseBranch: "",
      body: "",
      title: "",
    });
  const [pullRequestPreview, setPullRequestPreview] =
    useState<EnvironmentPullRequestPreview>();
  const [pullRequestResult, setPullRequestResult] =
    useState<EnvironmentPullRequestApplyResponse["result"]>();
  const [pullRequestNotice, setPullRequestNotice] = useState<string | null>(null);

  const commit = useMutation({
    mutationFn: async () => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      const preview =
        commitPreview ??
        (
          await mutateCockpit<EnvironmentCommitPreviewResponse>(
            "/api/environment/commit/preview",
            {
              author_email: commitDraft.authorEmail,
              author_name: commitDraft.authorName,
              message: commitDraft.message,
              session_id: sessionId,
            },
          )
        ).preview;
      return mutateCockpit<EnvironmentCommitApplyResponse>(
        "/api/environment/commit/apply",
        { preview_id: preview.id, session_id: sessionId },
      );
    },
    onSuccess: async (response) => {
      if (response.result === undefined) {
        setCommitPreview(response.preview);
        setCommitNotice(
          locale === "ru"
            ? "Подтвердите точный коммит во Inbox и примените снова."
            : "Approve the exact commit in Inbox, then apply again.",
        );
        openInbox();
        return;
      }
      setCommitPreview(undefined);
      setCommitDraft((current) => ({ ...current, message: "" }));
      setCommitNotice(
        `${message(locale, "environmentCommit")}: ${response.result.commit_head.slice(0, 8)}`,
      );
      await invalidateEnvironment(queryClient, sessionId);
    },
  });
  const push = useMutation({
    mutationFn: async () => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      const preview =
        pushPreview ??
        (
          await mutateCockpit<EnvironmentPushPreviewResponse>(
            "/api/environment/push/preview",
            { session_id: sessionId },
          )
        ).preview;
      return mutateCockpit<EnvironmentPushApplyResponse>(
        "/api/environment/push/apply",
        { preview_id: preview.id, session_id: sessionId },
      );
    },
    onSuccess: async (response) => {
      if (response.result === undefined) {
        setPushPreview(response.preview);
        setPushResult(undefined);
        setPushNotice(
          locale === "ru"
            ? "Подтвердите точный push во Inbox и примените снова."
            : "Approve the exact push in Inbox, then apply again.",
        );
        openInbox();
        return;
      }
      setPushPreview(undefined);
      setPushResult(response.result);
      setPushNotice(
        `${message(locale, "environmentPush")}: ${response.result.commit_head.slice(0, 8)}`,
      );
      await invalidateEnvironment(queryClient, sessionId);
    },
  });
  const pullRequest = useMutation({
    mutationFn: async () => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      const preview =
        pullRequestPreview ??
        (
          await mutateCockpit<EnvironmentPullRequestPreviewResponse>(
            "/api/environment/pull-request/preview",
            {
              base_branch: pullRequestDraft.baseBranch || undefined,
              body: pullRequestDraft.body,
              session_id: sessionId,
              title: pullRequestDraft.title,
            },
          )
        ).preview;
      return mutateCockpit<EnvironmentPullRequestApplyResponse>(
        "/api/environment/pull-request/apply",
        { preview_id: preview.id, session_id: sessionId },
      );
    },
    onSuccess: async (response) => {
      if (response.result === undefined) {
        setPullRequestPreview(response.preview);
        setPullRequestResult(undefined);
        setPullRequestNotice(
          locale === "ru"
            ? "Подтвердите точный pull request во Inbox и примените снова."
            : "Approve the exact pull request in Inbox, then apply again.",
        );
        openInbox();
        return;
      }
      setPullRequestPreview(undefined);
      setPullRequestResult(response.result);
      setPullRequestNotice(`PR #${response.result.number}`);
      await invalidateEnvironment(queryClient, sessionId);
    },
  });

  return {
    commitAction: {
      draft: commitDraft,
      error: commit.isError,
      notice: commit.isError
        ? errorMessage(commit.error, "Commit failed")
        : commitNotice,
      pending: commit.isPending,
      preview: commitPreview,
      setField: (field, value) => {
        setCommitDraft((current) => ({ ...current, [field]: value }));
        setCommitPreview(undefined);
        setCommitNotice(null);
        commit.reset();
      },
      submit: () => commit.mutate(),
    },
    pullRequestAction: {
      draft: pullRequestDraft,
      error: pullRequest.isError,
      notice: pullRequest.isError
        ? errorMessage(pullRequest.error, "Pull-request creation failed")
        : pullRequestNotice,
      pending: pullRequest.isPending,
      preview: pullRequestPreview,
      result: pullRequestResult,
      setField: (field, value) => {
        setPullRequestDraft((current) => ({ ...current, [field]: value }));
        setPullRequestPreview(undefined);
        setPullRequestNotice(null);
        pullRequest.reset();
      },
      submit: () => pullRequest.mutate(),
    },
    pushAction: {
      error: push.isError,
      notice: push.isError ? errorMessage(push.error, "Push failed") : pushNotice,
      pending: push.isPending,
      preview: pushPreview,
      result: pushResult,
      submit: () => push.mutate(),
    },
  };
}

export function EnvironmentCard({
  className = "",
  environment,
  error,
  commitAction,
  pushAction,
  pullRequestAction,
  locale,
  pending,
}: {
  className?: string;
  environment: EnvironmentView | undefined;
  error: boolean;
  commitAction: EnvironmentCommitAction;
  pushAction: EnvironmentPushAction;
  pullRequestAction: EnvironmentPullRequestAction;
  locale: LocalePreference;
  pending: boolean;
}) {
  return (
    <section
      className={`inspector-section environment-card ${className}`.trim()}
      data-state={environment?.status ?? "unavailable"}
    >
      <div className="environment-heading">
        <h3>{message(locale, "environment")}</h3>
        <span>{environment?.status ?? "unavailable"}</span>
      </div>
      {environment === undefined ? (
        <span className={error ? "mutation-error" : "muted-copy"}>
          {pending ? "…" : message(locale, "environmentUnavailable")}
        </span>
      ) : (
        <dl className="plan-fields">
          <div><dt>{message(locale, "changes")}</dt><dd>{environment.changes}</dd></div>
          <div><dt>{message(locale, "worktree")}</dt><dd title={environment.worktree}>{environment.worktree}</dd></div>
          <div><dt>{message(locale, "environmentBranch")}</dt><dd>{environment.branch} · {environment.head}</dd></div>
          <div><dt>{message(locale, "environmentCommit")}</dt><dd>{environment.commit}</dd></div>
          <div><dt>{message(locale, "environmentPush")}</dt><dd>{environment.push}</dd></div>
          <div><dt>{message(locale, "environmentIssuePr")}</dt><dd>{environment.issuePr}</dd></div>
          <div><dt>{message(locale, "environmentGitHub")}</dt><dd>{environment.githubRepository} · {environment.githubStatus}</dd></div>
          <div><dt>{message(locale, "environmentGitHubChecks")}</dt><dd>{environment.githubChecks}</dd></div>
          <div><dt>{message(locale, "environmentGitHubActions")}</dt><dd>{environment.githubActions}</dd></div>
          <div><dt>{message(locale, "environmentCaptured")}</dt><dd>{formatTimestamp(environment.capturedAt, locale)}</dd></div>
        </dl>
      )}
      <form
        className="environment-commit-form"
        onSubmit={(event) => {
          event.preventDefault();
          commitAction.submit();
        }}
      >
        <label>
          <span>{locale === "ru" ? "Сообщение коммита" : "Commit message"}</span>
          <input
            disabled={environment?.commit !== "ready" || commitAction.pending}
            maxLength={4096}
            onChange={(event) => commitAction.setField("message", event.target.value)}
            required
            value={commitAction.draft.message}
          />
        </label>
        <div>
          <label>
            <span>{locale === "ru" ? "Имя автора" : "Author name"}</span>
            <input
              disabled={environment?.commit !== "ready" || commitAction.pending}
              maxLength={200}
              onChange={(event) => commitAction.setField("authorName", event.target.value)}
              required
              value={commitAction.draft.authorName}
            />
          </label>
          <label>
            <span>{locale === "ru" ? "Email автора" : "Author email"}</span>
            <input
              disabled={environment?.commit !== "ready" || commitAction.pending}
              maxLength={200}
              onChange={(event) => commitAction.setField("authorEmail", event.target.value)}
              required
              type="email"
              value={commitAction.draft.authorEmail}
            />
          </label>
        </div>
        <button
          className="primary-button"
          disabled={environment?.commit !== "ready" || commitAction.pending}
          type="submit"
        >
          {message(
            locale,
            commitAction.preview === undefined
              ? "environmentCommit"
              : "apply",
          )}
        </button>
        {commitAction.notice === null ? null : (
          <p className={commitAction.error ? "mutation-error" : "mutation-success"}>
            {commitAction.notice}
          </p>
        )}
      </form>
      <section className="environment-push-action">
        {pushAction.preview === undefined ? null : (
          <dl className="plan-fields">
            <div><dt>Remote</dt><dd>{pushAction.preview.remote}</dd></div>
            <div><dt>Upstream</dt><dd>{pushAction.preview.upstream ?? "new"}</dd></div>
            <div><dt>{locale === "ru" ? "Целевая ветка" : "Target branch"}</dt><dd>{pushAction.preview.target_branch}</dd></div>
            <div><dt>HEAD</dt><dd><code>{pushAction.preview.head.slice(0, 12)}</code></dd></div>
            <div><dt>Remote HEAD</dt><dd><code>{pushAction.preview.remote_head?.slice(0, 12) ?? "new"}</code></dd></div>
          </dl>
        )}
        <button
          className="primary-button"
          disabled={environment?.push !== "ready" || pushAction.pending}
          onClick={pushAction.submit}
          type="button"
        >
          {message(locale, pushAction.preview === undefined ? "environmentPush" : "apply")}
        </button>
        {pushAction.notice === null ? null : (
          <p className={pushAction.error ? "mutation-error" : "mutation-success"}>
            {pushAction.notice}
          </p>
        )}
        {pushAction.result === undefined ? null : (
          <div className="environment-push-links">
            <a href={pushAction.result.remote_commit_url} rel="noreferrer" target="_blank">
              {locale === "ru" ? "Удалённый коммит" : "Remote commit"}
            </a>
            <a href={pushAction.result.run_evidence_url} rel="noreferrer" target="_blank">
              {locale === "ru" ? "Проверки и запуски" : "Checks and runs"}
            </a>
          </div>
        )}
      </section>
      <form
        className="environment-pull-request-action"
        onSubmit={(event) => {
          event.preventDefault();
          pullRequestAction.submit();
        }}
      >
        <label>
          <span>{locale === "ru" ? "Заголовок pull request" : "Pull-request title"}</span>
          <input
            disabled={environment?.push !== "ready" || pullRequestAction.pending}
            maxLength={256}
            onChange={(event) => pullRequestAction.setField("title", event.target.value)}
            required
            value={pullRequestAction.draft.title}
          />
        </label>
        <label>
          <span>{locale === "ru" ? "Описание" : "Body"}</span>
          <textarea
            disabled={environment?.push !== "ready" || pullRequestAction.pending}
            maxLength={16384}
            onChange={(event) => pullRequestAction.setField("body", event.target.value)}
            value={pullRequestAction.draft.body}
          />
        </label>
        <label>
          <span>{locale === "ru" ? "Базовая ветка" : "Base branch"}</span>
          <input
            disabled={environment?.push !== "ready" || pullRequestAction.pending}
            maxLength={512}
            onChange={(event) => pullRequestAction.setField("baseBranch", event.target.value)}
            placeholder={locale === "ru" ? "по умолчанию" : "repository default"}
            value={pullRequestAction.draft.baseBranch}
          />
        </label>
        {pullRequestAction.preview === undefined ? null : (
          <dl className="plan-fields">
            <div><dt>{locale === "ru" ? "Репозиторий" : "Repository"}</dt><dd>{pullRequestAction.preview.repository.name_with_owner}</dd></div>
            <div><dt>{locale === "ru" ? "Исходная ветка" : "Source branch"}</dt><dd>{pullRequestAction.preview.source_branch}</dd></div>
            <div><dt>{locale === "ru" ? "Базовая ветка" : "Base branch"}</dt><dd>{pullRequestAction.preview.base_branch}</dd></div>
            <div><dt>HEAD</dt><dd><code>{pullRequestAction.preview.source_head.slice(0, 12)}</code></dd></div>
            <div><dt>Base HEAD</dt><dd><code>{pullRequestAction.preview.base_head.slice(0, 12)}</code></dd></div>
          </dl>
        )}
        <button
          className="primary-button"
          disabled={environment?.push !== "ready" || pullRequestAction.pending}
          type="submit"
        >
          {pullRequestAction.preview === undefined
            ? (locale === "ru" ? "Создать pull request" : "Create pull request")
            : message(locale, "apply")}
        </button>
        {pullRequestAction.notice === null ? null : (
          <p className={pullRequestAction.error ? "mutation-error" : "mutation-success"}>
            {pullRequestAction.notice}
          </p>
        )}
        {pullRequestAction.result === undefined ? null : (
          <div className="environment-push-links">
            <a href={pullRequestAction.result.pull_request_url} rel="noreferrer" target="_blank">
              PR #{pullRequestAction.result.number}
            </a>
            <a href={pullRequestAction.result.commit_url} rel="noreferrer" target="_blank">Commit</a>
            <a href={pullRequestAction.result.checks_url} rel="noreferrer" target="_blank">Checks</a>
            <a href={pullRequestAction.result.run_evidence_url} rel="noreferrer" target="_blank">
              Actions
            </a>
          </div>
        )}
      </form>
    </section>
  );
}

export function MobileEnvironmentDisclosure({
  actions,
  environment,
  error,
  locale,
  pending,
}: {
  actions: ReturnType<typeof useEnvironmentActions>;
  environment: EnvironmentView | undefined;
  error: boolean;
  locale: LocalePreference;
  pending: boolean;
}) {
  return (
    <details className="mobile-environment">
      <summary>
        <strong>{message(locale, "environment")}</strong>
        <span>{environment?.status ?? "unavailable"}</span>
      </summary>
      <EnvironmentCard
        className="mobile-environment-card"
        commitAction={actions.commitAction}
        environment={environment}
        error={error}
        locale={locale}
        pending={pending}
        pullRequestAction={actions.pullRequestAction}
        pushAction={actions.pushAction}
      />
    </details>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

async function invalidateEnvironment(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string | undefined,
): Promise<void> {
  await queryClient.invalidateQueries({
    queryKey: requestKeys.environment(sessionId ?? "pending"),
  });
}

function openInbox(): void {
  globalThis.dispatchEvent(
    new CustomEvent("cockpit:open-inbox", { detail: "approvals" }),
  );
}
