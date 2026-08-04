import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ProviderAccountProjection } from "../../api/providers";
import { ProviderAccountCard } from "./ProviderAccountsSection";

describe("ProviderAccountCard", () => {
  it("allows reviewed Gemini login while unsupported status and logout stay disabled", () => {
    const markup = renderToStaticMarkup(
      <ProviderAccountCard
        account={account({
          actions: { cancel: false, logout: false, start: true, status: false },
          provider_id: "gemini-cli",
          display_name: "Gemini CLI",
          status: "unknown",
        })}
        actionPending={false}
        locale="en"
        onAction={() => undefined}
      />,
    );

    expect(markup).toContain("Gemini CLI");
    expect(markup).toContain(
      '<button disabled="" type="button">Refresh status</button>',
    );
    expect(markup).toContain(
      '<button type="button">Start provider login</button>',
    );
    expect(markup).toContain(
      '<button disabled="" type="button">Log out</button>',
    );
  });

  it("offers exact cancellation while a provider login is pending", () => {
    const markup = renderToStaticMarkup(
      <ProviderAccountCard
        account={account({
          actions: { cancel: true, logout: false, start: true, status: false },
          status: "pending",
        })}
        actionPending={false}
        locale="en"
        onAction={() => undefined}
      />,
    );

    expect(markup).toContain('<button type="button">Cancel login</button>');
    expect(markup).not.toContain("Start provider login");
    expect(markup).toContain("Complete it in the provider browser or terminal");
  });
});

function account(
  overrides: Partial<ProviderAccountProjection> = {},
): ProviderAccountProjection {
  return {
    actions: { cancel: false, logout: true, start: true, status: true },
    attempt_id: null,
    authentication_method: null,
    checked_at: "2026-08-04T12:00:00Z",
    credential_values_readable: false,
    detected_cli_version: "0.146.0",
    display_name: "Codex CLI",
    expires_at: null,
    home_scope: "isolated_provider_owned",
    identity_label: null,
    pinned_cli_version: "0.146.0",
    provider_id: "codex-cli",
    reason_code: "provider_logged_out",
    recovery: ["retry provider-owned login"],
    source: "reviewed_provider_authentication_evidence_v1",
    status: "logged_out",
    version_status: "reviewed_pin",
    ...overrides,
  };
}
