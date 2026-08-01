import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, mutateCockpit } from "./core";

export interface CredentialScopeProjection {
  audiences: string[];
  resources: string[];
  operation_classes: string[];
  scope_digest: string;
}

export interface CredentialQuotaProjection {
  status: "known" | "unknown" | "not_applicable";
  unit: string | null;
  limit: number | null;
  remaining: number | null;
  resets_at: string | null;
  reason_code: string | null;
}

export interface CredentialSourceProjection {
  source_id: string;
  broker_id: string;
  provider_identity_ref: string;
  reference_kind: "environment" | "keychain" | "test";
  scope: CredentialScopeProjection;
  quota: CredentialQuotaProjection;
  expires_at: string | null;
  state: "current" | "expired";
  demo: boolean;
}

export interface CredentialLeaseProjection {
  lease_id: string;
  broker_id: string;
  audience: string;
  resource: string;
  operation_class: string;
  issued_at: string;
  expires_at: string;
  parent_run_id: string;
  scope_digest: string;
  status: "active" | "revoked" | "expired";
}

export interface CredentialOperatorSnapshot {
  schema_version: 1;
  revision: string;
  broker_id: string;
  sources: CredentialSourceProjection[];
  leases: CredentialLeaseProjection[];
  content_free: true;
}

export interface CredentialOperatorAction {
  schema_version: 1;
  state: "admitted" | "denied" | "stale" | "revoked" | "already_terminal";
  reason_code: string;
  snapshot: CredentialOperatorSnapshot;
  receipt: {
    receipt_id: string;
    lease_id: string;
    status: "revoked" | "already_terminal";
    reason_code: string;
    revoked_at: string;
    idempotency_key_digest: string;
    content_free: true;
  } | null;
}

const credentialRootKey = ["cockpit", "credential-operator"] as const;

export const credentialKeys = {
  snapshot: () => [...credentialRootKey, "snapshot"] as const,
};

export function credentialSnapshotOptions() {
  return queryOptions({
    queryFn: ({ signal }) => fetchCockpit<CredentialOperatorSnapshot>("/api/credentials", signal),
    queryKey: credentialKeys.snapshot(),
    staleTime: 5_000,
  });
}

export function requestCredentialLease(
  snapshot: CredentialOperatorSnapshot,
  source: CredentialSourceProjection,
): Promise<CredentialOperatorAction> {
  return mutateCockpit<CredentialOperatorAction>("/api/credentials/leases", {
    audience: source.scope.audiences[0],
    expected_revision: snapshot.revision,
    operation_class: source.scope.operation_classes[0],
    parent_run_id: "credential-demo-run",
    resource: source.scope.resources[0],
    source_id: source.source_id,
    ttl_seconds: 300,
  });
}

export function revokeCredentialLease(
  snapshot: CredentialOperatorSnapshot,
  leaseId: string,
): Promise<CredentialOperatorAction> {
  return mutateCockpit<CredentialOperatorAction>(
    `/api/credentials/leases/${encodeURIComponent(leaseId)}/revoke`,
    { confirmed: true, expected_revision: snapshot.revision },
  );
}
