import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  credentialKeys,
  credentialSnapshotOptions,
  requestCredentialLease,
  revokeCredentialLease,
  type CredentialLeaseProjection,
  type CredentialOperatorAction,
  type CredentialSourceProjection,
} from "../../api/credentials";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { usePreferences } from "../../preferences-context";
import { Boundary, Fact, SectionError, SectionPending } from "./shared";

type CredentialAction =
  | { kind: "request"; source: CredentialSourceProjection }
  | { kind: "revoke"; leaseId: string };

export default function CredentialLeasesSection() {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const snapshotQuery = useQuery(credentialSnapshotOptions());
  const [notice, setNotice] = useState<CredentialOperatorAction | null>(null);
  const [confirmingLeaseId, setConfirmingLeaseId] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: (operation: CredentialAction) => {
      const snapshot = snapshotQuery.data;
      if (snapshot === undefined) throw new Error("Credential snapshot is unavailable");
      return operation.kind === "request"
        ? requestCredentialLease(snapshot, operation.source)
        : revokeCredentialLease(snapshot, operation.leaseId);
    },
    onSuccess: (result) => {
      queryClient.setQueryData(credentialKeys.snapshot(), result.snapshot);
      setConfirmingLeaseId(null);
      setNotice(result);
    },
  });

  if (snapshotQuery.isPending) return <SectionPending locale={locale} />;
  if (snapshotQuery.isError || snapshotQuery.data === undefined) {
    return <SectionError error={snapshotQuery.error} locale={locale} />;
  }
  const snapshot = snapshotQuery.data;
  return (
    <>
      <div className="credential-operator-heading">
        <div>
          <strong>{snapshot.broker_id}</strong>
          <span>{message(locale, "credentialMetadataOnly")}</span>
        </div>
        <span className="content-free-badge">{message(locale, "contentFreeProjection")}</span>
      </div>
      <div className="credential-source-grid">
        {snapshot.sources.map((source) => (
          <CredentialSourceCard
            key={source.source_id}
            locale={locale}
            onRequest={() => action.mutate({ kind: "request", source })}
            requestPending={
              action.isPending
              && action.variables?.kind === "request"
              && action.variables.source.source_id === source.source_id
            }
            source={source}
          />
        ))}
      </div>
      <h3 className="settings-subheading">{message(locale, "credentialLeases")}</h3>
      {snapshot.leases.length === 0 ? (
        <p className="credential-empty-state">{message(locale, "noCredentialLeases")}</p>
      ) : (
        <div className="credential-lease-grid">
          {snapshot.leases.map((lease) => (
            <CredentialLeaseCard
              key={lease.lease_id}
              confirming={confirmingLeaseId === lease.lease_id}
              lease={lease}
              locale={locale}
              onBeginRevoke={() => setConfirmingLeaseId(lease.lease_id)}
              onCancelRevoke={() => setConfirmingLeaseId(null)}
              onRevoke={() => action.mutate({ kind: "revoke", leaseId: lease.lease_id })}
              revokePending={
                action.isPending
                && action.variables?.kind === "revoke"
                && action.variables.leaseId === lease.lease_id
              }
            />
          ))}
        </div>
      )}
      {notice === null ? null : (
        <p
          className={notice.state === "denied" || notice.state === "stale" ? "mutation-error" : "mutation-success"}
          role="status"
        >
          {actionMessage(locale, notice)}
        </p>
      )}
      {action.isError ? (
        <p className="mutation-error" role="alert">
          {message(locale, "credentialActionFailed")}
        </p>
      ) : null}
      <Boundary effect="lease_scoped" source="fake_broker" />
    </>
  );
}

export function CredentialSourceCard({
  locale,
  onRequest,
  requestPending,
  source,
}: {
  locale: LocalePreference;
  onRequest: () => void;
  requestPending: boolean;
  source: CredentialSourceProjection;
}) {
  const scope = source.scope;
  const requestable =
    source.state === "current"
    && scope.audiences.length > 0
    && scope.resources.length > 0
    && scope.operation_classes.length > 0;
  return (
    <article className="credential-card" data-state={source.state}>
      <header>
        <div>
          <strong>{source.provider_identity_ref}</strong>
          <small>{source.source_id}</small>
        </div>
        <span className={`status-label ${source.state === "current" ? "success" : ""}`}>
          {source.demo ? message(locale, "fakeBrokerDemo") : source.state}
        </span>
      </header>
      <dl className="settings-facts">
        <Fact label={message(locale, "source")} mono value={source.broker_id} />
        <Fact label={message(locale, "referenceType")} value={source.reference_kind} />
        <Fact label={message(locale, "audience")} value={scope.audiences.join(", ")} />
        <Fact label={message(locale, "resource")} value={scope.resources.join(", ")} />
        <Fact label={message(locale, "operationClass")} value={scope.operation_classes.join(", ")} />
        <Fact label={message(locale, "expiry")} value={source.expires_at ?? message(locale, "noExpiry")} />
      </dl>
      <button disabled={!requestable || requestPending} onClick={onRequest} type="button">
        {requestPending ? message(locale, "requestingLease") : message(locale, "requestLease")}
      </button>
    </article>
  );
}

export function CredentialLeaseCard({
  confirming,
  lease,
  locale,
  onBeginRevoke,
  onCancelRevoke,
  onRevoke,
  revokePending,
}: {
  confirming: boolean;
  lease: CredentialLeaseProjection;
  locale: LocalePreference;
  onBeginRevoke: () => void;
  onCancelRevoke: () => void;
  onRevoke: () => void;
  revokePending: boolean;
}) {
  return (
    <article className="credential-card credential-lease-card" data-state={lease.status}>
      <header>
        <div>
          <strong>{lease.operation_class}</strong>
          <small>{lease.lease_id}</small>
        </div>
        <span className={`status-label ${lease.status === "active" ? "success" : ""}`}>
          {lease.status}
        </span>
      </header>
      <dl className="settings-facts">
        <Fact label={message(locale, "audience")} value={lease.audience} />
        <Fact label={message(locale, "resource")} value={lease.resource} />
        <Fact label={message(locale, "parentRun")} mono value={lease.parent_run_id} />
        <Fact label={message(locale, "expiry")} value={lease.expires_at} />
      </dl>
      {confirming && lease.status === "active" ? (
        <div className="credential-revoke-confirmation" role="group">
          <button disabled={revokePending} onClick={onRevoke} type="button">
            {revokePending ? message(locale, "revokingLease") : message(locale, "confirmRevokeLease")}
          </button>
          <button disabled={revokePending} onClick={onCancelRevoke} type="button">
            {message(locale, "cancel")}
          </button>
        </div>
      ) : (
        <button disabled={lease.status !== "active" || revokePending} onClick={onBeginRevoke} type="button">
          {message(locale, "revokeLease")}
        </button>
      )}
    </article>
  );
}

function actionMessage(locale: LocalePreference, action: CredentialOperatorAction) {
  if (action.state === "stale") return message(locale, "credentialSnapshotStale");
  if (action.state === "denied") return `${message(locale, "credentialLeaseDenied")}: ${action.reason_code}`;
  if (action.state === "admitted") return message(locale, "credentialLeaseRequested");
  if (action.state === "revoked") return message(locale, "credentialLeaseRevoked");
  return message(locale, "credentialLeaseAlreadyTerminal");
}
