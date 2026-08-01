import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mutateCockpit } from "../../api/core";
import type {
  ProviderAccountMutationResponse,
  ProviderAccountProjection,
} from "../../api/providers";
import { settingsProviderAccountsOptions } from "../../api/queries/settings";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { usePreferences } from "../../preferences-context";
import { invalidateSettingsProviderAccounts } from "./invalidation";
import { Boundary, Fact, SectionError, SectionPending } from "./shared";

type ProviderAccountAction = "cancel" | "login" | "logout" | "refresh";

export default function ProviderAccountsSection() {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const accounts = useQuery(settingsProviderAccountsOptions());
  const action = useMutation({
    mutationFn: ({
      providerId,
      action: operation,
    }: {
      providerId: string;
      action: ProviderAccountAction;
    }) =>
      mutateCockpit<ProviderAccountMutationResponse>(
        `/api/provider-accounts/${encodeURIComponent(providerId)}/${operation}`,
      ),
    onSuccess: () => invalidateSettingsProviderAccounts(queryClient),
  });

  if (accounts.isPending) return <SectionPending locale={locale} />;
  if (accounts.isError || accounts.data === undefined) {
    return <SectionError error={accounts.error} locale={locale} />;
  }
  return (
    <>
      <div className="provider-account-grid">
        {accounts.data.accounts.map((account) => (
          <ProviderAccountCard
            account={account}
            actionPending={
              action.isPending && action.variables?.providerId === account.provider_id
            }
            key={account.provider_id}
            locale={locale}
            onAction={(operation) =>
              action.mutate({ providerId: account.provider_id, action: operation })
            }
          />
        ))}
      </div>
      {action.isError ? (
        <p className="mutation-error" role="alert">
          {message(locale, "loginActionFailed")}
        </p>
      ) : null}
      <Boundary effect="isolated_home_only" source="provider_owned_cli" />
    </>
  );
}

function ProviderAccountCard({
  account,
  actionPending,
  locale,
  onAction,
}: {
  account: ProviderAccountProjection;
  actionPending: boolean;
  locale: LocalePreference;
  onAction: (action: ProviderAccountAction) => void;
}) {
  const pending = account.status === "pending";
  return (
    <article className="provider-account-card">
      <header>
        <div>
          <strong>{account.display_name}</strong>
          <small>{account.provider_id}</small>
        </div>
        <span className={`status-label ${account.status === "ready" ? "success" : ""}`}>
          {account.status}
        </span>
      </header>
      <dl className="settings-facts">
        <Fact label={message(locale, "source")} mono value={account.source} />
        <Fact
          label={message(locale, "detectedVersion")}
          value={account.detected_cli_version ?? message(locale, "cliNotDetected")}
        />
        <Fact
          label={message(locale, "authentication")}
          value={
            account.authentication_method
            ?? message(locale, "providerOwnedCredentials")
          }
        />
        <Fact
          label={message(locale, "expiry")}
          value={account.expires_at ?? message(locale, "noExpiry")}
        />
      </dl>
      <p className="provider-account-recovery">
        <strong>{message(locale, "accountRecovery")}</strong>
        <span>{account.recovery[0] ?? account.reason_code}</span>
      </p>
      {pending ? (
        <p className="settings-action-result" role="status">
          {message(locale, "loginPendingHint")}
        </p>
      ) : null}
      <div className="provider-actions">
        <button
          disabled={!account.actions.status || actionPending || pending}
          onClick={() => onAction("refresh")}
          type="button"
        >
          {message(locale, "refreshStatus")}
        </button>
        {pending ? (
          <button
            disabled={!account.actions.cancel || actionPending}
            onClick={() => onAction("cancel")}
            type="button"
          >
            {message(locale, "cancelLogin")}
          </button>
        ) : (
          <button
            disabled={!account.actions.start || actionPending}
            onClick={() => onAction("login")}
            type="button"
          >
            {message(locale, "startLogin")}
          </button>
        )}
        <button
          disabled={!account.actions.logout || actionPending || pending}
          onClick={() => onAction("logout")}
          type="button"
        >
          {message(locale, "logout")}
        </button>
      </div>
    </article>
  );
}
