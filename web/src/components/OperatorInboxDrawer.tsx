import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import {
  CockpitApiError,
  mutateCockpit,
  type ActionInboxCommand,
  type ActionInboxItem,
  type ActionInboxKind,
  type ActionInboxResponse,
} from "../api";
import {
  actionIsDangerous,
  actionNeedsAnswer,
  buildActionInboxResponse,
  flattenActionInboxPages,
  humanizeAction,
} from "../features/operator-workspace/action-inbox-model";
import { message } from "../messages";
import { usePreferences } from "../preferences-context";
import { operatorInboxOptions, requestKeys } from "../request-graph";
import { formatTimestamp, statusTone } from "../surface-model";

const inboxKinds: readonly ActionInboxKind[] = [
  "approval",
  "automation_question",
  "mcp_elicitation",
  "provider_login",
  "run_input",
];

export default function OperatorInboxDrawer({
  onClose,
  workspaceId,
}: {
  onClose: () => void;
  workspaceId: string;
}) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        aria-label={message(locale, "actionInbox")}
        aria-modal="true"
        className="inbox-drawer operator-inbox-drawer"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="drawer-heading">
          <div>
            <p className="section-kicker">{message(locale, "globalInbox")}</p>
            <h2>{message(locale, "actionInbox")}</h2>
          </div>
          <button
            aria-label={message(locale, "close")}
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </div>
        <OperatorInboxPanel onNavigate={onClose} workspaceId={workspaceId} />
      </aside>
    </div>
  );
}

export function OperatorInboxPanel({
  onNavigate,
  workspaceId,
}: {
  onNavigate?: () => void;
  workspaceId: string;
}) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ActionInboxKind | "all">("all");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const query = useInfiniteQuery({
    ...operatorInboxOptions(workspaceId, kind === "all" ? [] : [kind]),
    enabled: workspaceId !== "",
  });
  const items = useMemo(
    () => flattenActionInboxPages(query.data?.pages ?? []),
    [query.data?.pages],
  );
  const response = useMutation({
    mutationFn: async ({
      action,
      item,
    }: {
      action: ActionInboxCommand;
      item: ActionInboxItem;
    }) => {
      const payload = await buildActionInboxResponse(
        item,
        action,
        answers[item.item_id] ?? "",
      );
      return mutateCockpit<ActionInboxResponse>(
        `/api/operator/inbox/${encodeURIComponent(item.authority)}/${encodeURIComponent(item.item_id)}/responses`,
        { ...payload },
      );
    },
    onSuccess: async (_result, variables) => {
      setAnswers((current) => {
        const next = { ...current };
        delete next[variables.item.item_id];
        return next;
      });
      await queryClient.invalidateQueries({
        queryKey: requestKeys.operatorInboxScope(workspaceId),
      });
    },
    onError: async (error) => {
      if (error instanceof CockpitApiError && error.status === 409) {
        await queryClient.invalidateQueries({
          queryKey: requestKeys.operatorInboxScope(workspaceId),
        });
      }
    },
  });

  return (
    <section aria-label={message(locale, "actionInbox")} className="operator-inbox-panel">
      <p className="operator-inbox-detail">
        {message(locale, "actionInboxDetail")}
      </p>
      <label className="field-control operator-inbox-filter">
        <span>{message(locale, "actionKind")}</span>
        <select
          disabled={workspaceId === ""}
          onChange={(event) =>
            setKind(event.target.value as ActionInboxKind | "all")
          }
          value={kind}
        >
          <option value="all">{message(locale, "allActions")}</option>
          {inboxKinds.map((item) => (
            <option key={item} value={item}>{humanizeAction(item)}</option>
          ))}
        </select>
      </label>
      <div className="inbox-list operator-inbox-list">
          {workspaceId === "" ? (
            <div className="empty-state">{message(locale, "operatorInboxUnavailable")}</div>
          ) : null}
          {workspaceId !== "" && query.isPending ? (
            <div className="skeleton-block" aria-label={message(locale, "loading")} />
          ) : null}
          {query.isError ? (
            <div className="operator-inbox-error" role="alert">
              <strong>
                {query.error instanceof CockpitApiError && query.error.status === 409
                  ? message(locale, "actionInboxResnapshot")
                  : message(locale, "operatorInboxUnavailable")}
              </strong>
              <button onClick={() => void query.refetch()} type="button">
                {message(locale, "resyncCursor")}
              </button>
            </div>
          ) : null}
          {query.isSuccess && items.length === 0 ? (
            <div className="empty-state">{message(locale, "noPendingActions")}</div>
          ) : null}
          {items.map((item) => (
            <OperatorInboxItemCard
              answer={answers[item.item_id] ?? ""}
              item={item}
              key={`${item.authority}:${item.item_id}`}
              locale={locale}
              mutationError={
                response.variables?.item.item_id === item.item_id
                  ? response.error
                  : null
              }
              mutationPending={
                response.isPending
                && response.variables?.item.item_id === item.item_id
              }
              onAction={(action) => response.mutate({ action, item })}
              onAnswer={(answer) =>
                setAnswers((current) => ({ ...current, [item.item_id]: answer }))
              }
              onNavigate={onNavigate}
            />
          ))}
          {query.hasNextPage ? (
            <button
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
              type="button"
            >
              {message(
                locale,
                query.isFetchingNextPage ? "loading" : "loadMoreActions",
              )}
            </button>
          ) : null}
      </div>
    </section>
  );
}

function OperatorInboxItemCard({
  answer,
  item,
  locale,
  mutationError,
  mutationPending,
  onAction,
  onAnswer,
  onNavigate,
}: {
  answer: string;
  item: ActionInboxItem;
  locale: "en" | "ru";
  mutationError: Error | null;
  mutationPending: boolean;
  onAction: (action: ActionInboxCommand) => void;
  onAnswer: (answer: string) => void;
  onNavigate?: () => void;
}) {
  return (
    <article className="inbox-item operator-inbox-item">
      <div className="inbox-item-heading">
        <div>
          <strong>{humanizeAction(item.kind)}</strong>
          <span>{item.authority}</span>
        </div>
        <span className={`status-label ${statusTone(item.consequence)}`}>
          {humanizeAction(item.consequence)}
        </span>
      </div>
      <dl className="compact-fields operator-inbox-bindings">
        <div><dt>{message(locale, "actionOrigin")}</dt><dd>{item.origin}</dd></div>
        <div><dt>{message(locale, "revision")}</dt><dd>{item.revision}</dd></div>
        <div><dt>{message(locale, "digest")}</dt><dd>{item.item_sha256.slice(0, 12)}</dd></div>
        <div><dt>{message(locale, "created")}</dt><dd>{formatTimestamp(item.created_at, locale)}</dd></div>
      </dl>
      {item.response_schema === null ? null : (
        <label className="field-control">
          <span>{humanizeAction(item.response_schema)}</span>
          <textarea
            maxLength={4096}
            onChange={(event) => onAnswer(event.target.value)}
            placeholder={message(locale, "actionResponsePlaceholder")}
            value={answer}
          />
        </label>
      )}
      {mutationError === null ? null : (
        <div className="error-state" role="alert">
          {mutationError instanceof CockpitApiError && mutationError.status === 409
            ? message(locale, "actionInboxResnapshot")
            : message(locale, "actionResponseFailed")}
        </div>
      )}
      <div className="operator-inbox-links">
        {item.run_id === null ? null : (
          <Link
            onClick={onNavigate}
            params={{ runId: item.run_id }}
            to="/web/runs/$runId"
          >
            {message(locale, "openRun")}
          </Link>
        )}
        {item.expires_at === null ? null : (
          <span>{formatTimestamp(item.expires_at, locale)}</span>
        )}
      </div>
      <div className="decision-actions">
        {item.allowed_actions.map((action) => (
          <button
            className={actionIsDangerous(action) ? "danger-button" : "primary-button"}
            disabled={
              mutationPending
              || (actionNeedsAnswer(action) && answer.trim() === "")
            }
            key={action}
            onClick={() => onAction(action)}
            type="button"
          >
            {humanizeAction(action)}
          </button>
        ))}
      </div>
    </article>
  );
}
