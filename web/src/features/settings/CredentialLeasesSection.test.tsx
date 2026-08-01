import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  CredentialLeaseProjection,
  CredentialSourceProjection,
} from "../../api/credentials";
import {
  CredentialLeaseCard,
  CredentialSourceCard,
} from "./CredentialLeasesSection";

const source: CredentialSourceProjection = {
  broker_id: "fake-broker",
  demo: true,
  expires_at: null,
  provider_identity_ref: "fake-provider@demo-v1",
  quota: {
    limit: null,
    reason_code: "hermetic_demo",
    remaining: null,
    resets_at: null,
    status: "not_applicable",
    unit: null,
  },
  reference_kind: "test",
  scope: {
    audiences: ["api.example.test"],
    operation_classes: ["issue.write"],
    resources: ["gigaloom/demo"],
    scope_digest: "1".repeat(64),
  },
  source_id: "fake-github-demo",
  state: "current",
};

const lease: CredentialLeaseProjection = {
  audience: "api.example.test",
  broker_id: "fake-broker",
  expires_at: "2026-08-01T18:05:00+00:00",
  issued_at: "2026-08-01T18:00:00+00:00",
  lease_id: "credential-lease-demo",
  operation_class: "issue.write",
  parent_run_id: "credential-demo-run",
  resource: "gigaloom/demo",
  scope_digest: "1".repeat(64),
  status: "active",
};

describe("credential lease operator cards", () => {
  it("shows source, scope, expiry, and an explicit request without a credential value", () => {
    const markup = renderToStaticMarkup(
      <CredentialSourceCard
        locale="en"
        onRequest={vi.fn()}
        requestPending={false}
        source={source}
      />,
    );

    expect(markup).toContain("Fake broker demo");
    expect(markup).toContain("api.example.test");
    expect(markup).toContain("issue.write");
    expect(markup).toContain("Request 5-minute lease");
    expect(markup).not.toContain("secret_ref_id");
    expect(markup).not.toContain("GIGALOOM_FAKE_BROKER_DEMO");
  });

  it("shows typed lease state and exposes revocation only for an active lease", () => {
    const active = renderToStaticMarkup(
      <CredentialLeaseCard
        confirming={false}
        lease={lease}
        locale="en"
        onBeginRevoke={vi.fn()}
        onCancelRevoke={vi.fn()}
        onRevoke={vi.fn()}
        revokePending={false}
      />,
    );
    const expired = renderToStaticMarkup(
      <CredentialLeaseCard
        confirming={false}
        lease={{ ...lease, status: "expired" }}
        locale="en"
        onBeginRevoke={vi.fn()}
        onCancelRevoke={vi.fn()}
        onRevoke={vi.fn()}
        revokePending={false}
      />,
    );

    expect(active).toContain("active");
    expect(active).toContain("Revoke lease");
    expect(active).not.toContain("disabled");
    expect(expired).toContain("expired");
    expect(expired).toContain("disabled");
  });

  it("uses an inline second step before sending revocation", () => {
    const markup = renderToStaticMarkup(
      <CredentialLeaseCard
        confirming
        lease={lease}
        locale="en"
        onBeginRevoke={vi.fn()}
        onCancelRevoke={vi.fn()}
        onRevoke={vi.fn()}
        revokePending={false}
      />,
    );

    expect(markup).toContain("Confirm revoke");
    expect(markup).toContain("Cancel");
    expect(markup).not.toContain("Revoke this exact");
  });
});
