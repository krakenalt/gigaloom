import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  AgentInstallPreviewResponse,
  AgentInstallationOperationResponse,
  AgentRegistryEntryProjection,
  InstalledAgentProjection,
} from "../../api/agentRuntimes";
import { InstalledAgentCard, RegistryAgentCard } from "./AgentCards";
import { AgentInstallDrawer } from "./InstallDrawer";

const agent: AgentRegistryEntryProjection = {
  description: "A generic registry fixture",
  distribution_kinds: ["binary", "npx"],
  distributions: [
    {
      architecture: "aarch64",
      integrity: "verified",
      kind: "binary",
      package_or_archive: "generic.zip",
      platform: "darwin",
    },
  ],
  entry_digest: "1".repeat(64),
  integrity: "verified",
  icon_ref: "https://cdn.agentclientprotocol.com/generic-agent.svg",
  license: "MIT",
  name: "Generic coding agent",
  platforms: ["darwin-aarch64"],
  registry_id: "generic-agent",
  repository: "https://example.test/source",
  version: "1.0.0",
  website: null,
};

const readyPreview: AgentInstallPreviewResponse = {
  browser_selected_distribution: false,
  collision_namespaces: [],
  decisions: [{
    distribution_digest: "2".repeat(64),
    rank: 0,
    reason_code: "distribution_selected",
    status: "selected",
  }],
  installation_started: false,
  local_agent_id: "generic-agent",
  plan: {
    architecture: "aarch64",
    confirmation_required: true,
    distribution_kind: "binary",
    entry_digest: "1".repeat(64),
    expires_at: "2026-08-03T12:10:00Z",
    integrity_policy: "require_verified",
    lifecycle_script_policy: "disabled",
    local_agent_id: "generic-agent",
    package_or_archive: "generic.zip",
    plan_id: "plan-generic",
    platform: "darwin",
    registry_id: "generic-agent",
    side_effects: ["bounded_https_download", "private_archive_extraction"],
    snapshot_digest: "3".repeat(64),
    version: "1.0.0",
  },
  proposed_local_agent_id: null,
  reason_code: "distribution_selected",
  schema_version: 1,
};

const runningOperation: AgentInstallationOperationResponse = {
  content_free: true,
  events: [
    { observed_at: "2026-08-03T12:00:00Z", reason_code: "install_queued", sequence: 0, state: "queued" },
    { observed_at: "2026-08-03T12:00:01Z", reason_code: "registry_entry_selected", sequence: 1, state: "resolving" },
    { observed_at: "2026-08-03T12:00:02Z", reason_code: "distribution_selected", sequence: 2, state: "planned" },
    { observed_at: "2026-08-03T12:00:03Z", reason_code: "install_started", sequence: 3, state: "installing" },
  ],
  kind: "install",
  operation_id: "agent-op-generic",
  registry_or_local_id: "generic-agent",
  requested_local_agent_id: null,
  result_active: null,
  result_install_id: null,
  result_local_agent_id: null,
  result_version: null,
  schema_version: 1,
  status: "installing",
  terminal: false,
  terminal_reason_code: null,
};

describe("coding-agent marketplace components", () => {
  it("shows registry facts and asks the backend for a preview", () => {
    const markup = renderToStaticMarkup(<RegistryAgentCard agent={agent} onPreview={vi.fn()} />);

    expect(markup).toContain("Verified metadata");
    expect(markup).toContain("darwin-aarch64");
    expect(markup).toContain("Review install");
    expect(markup).toContain("cdn.agentclientprotocol.com/generic-agent.svg");
    expect(markup).toContain('referrerPolicy="no-referrer"');
    expect(markup).not.toContain("Install now");
  });

  it("renders collision evidence and the server-proposed safe alias", () => {
    const preview: AgentInstallPreviewResponse = {
      browser_selected_distribution: false,
      collision_namespaces: ["native_agent"],
      decisions: [{
        distribution_digest: "2".repeat(64),
        rank: 0,
        reason_code: "identity_collision_requires_explicit_alias",
        status: "rejected",
      }],
      installation_started: false,
      local_agent_id: null,
      plan: null,
      proposed_local_agent_id: "generic-agent-acp",
      reason_code: "identity_collision_requires_explicit_alias",
      schema_version: 1,
    };
    const markup = renderToStaticMarkup(
      <AgentInstallDrawer
        agent={agent}
        alias=""
        allowUnverified={false}
        installError={null}
        installPending={false}
        operation={null}
        preview={preview}
        previewPending={false}
        onAliasChange={vi.fn()}
        onAllowUnverifiedChange={vi.fn()}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        onPreview={vi.fn()}
      />,
    );

    expect(markup).toContain("native_agent");
    expect(markup).toContain("generic-agent-acp");
    expect(markup).not.toContain("Confirm one transaction");
  });

  it("renders an install authority rejection", () => {
    const markup = renderToStaticMarkup(
      <AgentInstallDrawer
        agent={agent}
        alias=""
        allowUnverified={false}
        installError="Install unavailable: managed-agent network isolation authority is missing."
        installPending={false}
        operation={null}
        preview={null}
        previewPending={false}
        onAliasChange={vi.fn()}
        onAllowUnverifiedChange={vi.fn()}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        onPreview={vi.fn()}
      />,
    );

    expect(markup).toContain("Install unavailable");
    expect(markup).toContain("network isolation authority");
  });

  it("explains the managed installation flow in user-facing language", () => {
    const markup = renderToStaticMarkup(
      <AgentInstallDrawer
        agent={agent}
        alias=""
        allowUnverified={false}
        installError={null}
        installPending={false}
        operation={runningOperation}
        preview={readyPreview}
        previewPending={false}
        onAliasChange={vi.fn()}
        onAllowUnverifiedChange={vi.fn()}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        onPreview={vi.fn()}
      />,
    );

    expect(markup).toContain("Installing Generic coding agent");
    expect(markup).toContain("Step 2 of 4");
    expect(markup).toContain("Download, verify, and unpack");
    expect(markup).toContain("What this installation changes");
    expect(markup).toContain("Global npm/Python packages");
    expect(markup).toContain("Run a safe compatibility check");
    expect(markup).toContain("Technical details");
    expect(markup).toContain("install_started");
    expect(markup).toContain("Cancel installation");
    expect(markup).toContain("next safe checkpoint");
  });

  it("turns a binary download failure into actionable copy", () => {
    const failedOperation: AgentInstallationOperationResponse = {
      ...runningOperation,
      events: [
        ...runningOperation.events,
        { observed_at: "2026-08-03T12:00:04Z", reason_code: "binary_download_failed", sequence: 4, state: "failed" },
      ],
      status: "failed",
      terminal: true,
      terminal_reason_code: "binary_download_failed",
    };
    const markup = renderToStaticMarkup(
      <AgentInstallDrawer
        agent={agent}
        alias=""
        allowUnverified={false}
        installError={null}
        installPending={false}
        operation={failedOperation}
        preview={readyPreview}
        previewPending={false}
        onAliasChange={vi.fn()}
        onAllowUnverifiedChange={vi.fn()}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        onPreview={vi.fn()}
      />,
    );

    expect(markup).toContain("Could not install Generic coding agent");
    expect(markup).toContain("Download failed");
    expect(markup).toContain("No agent was activated");
    expect(markup).toContain("check network or proxy access");
    expect(markup).toContain(">Close<");
    expect(markup).not.toContain("Cancel installation");
  });

  it("exposes probe, auth, update, rollback, remove, and use actions", () => {
    const installed: InstalledAgentProjection = {
      activation_status: "degraded",
      active: true,
      auth_required: true,
      distribution_kind: "npx",
      install_id: "install-generic",
      local_agent_id: "generic-agent",
      probe_state: "auth_required",
      readiness: {
        acp_transport: "ready",
        action: "select_gateway_route",
        gateway_availability: "available",
        native_launch_available: true,
        protocols: ["openai_chat_completions"],
        provider_bridge: "ready",
        reason_ids: [],
        schema_version: 1,
        status: "ready",
      },
      registry_id: "generic-agent",
      update_available: true,
      version: "1.0.0",
    };
    const markup = renderToStaticMarkup(
      <InstalledAgentCard
        agent={installed}
        busyAction={null}
        probe={null}
        onActivate={vi.fn()}
        onProbe={vi.fn()}
        onRemove={vi.fn()}
        onRollback={vi.fn()}
        onUpdate={vi.fn()}
        onUse={vi.fn()}
      />,
    );

    for (const label of ["Reprobe", "Update", "Rollback", "Remove", "Use in new run"]) {
      expect(markup).toContain(label);
    }
    expect(markup).toContain("Authentication");
    expect(markup).toContain("Required");
    expect(markup).toContain("Provider bridge");
    expect(markup).toContain("Gateway routes are available");
    expect(markup).toContain("provider default");
  });

  it("explains and exposes safe activation for every inactive managed ACP revision", () => {
    const installed: InstalledAgentProjection = {
      activation_status: "inactive",
      active: false,
      auth_required: false,
      distribution_kind: "uvx",
      install_id: "install-future-acp",
      local_agent_id: "future-acp",
      probe_state: "unavailable",
      readiness: {
        acp_transport: "ready",
        action: "activate",
        gateway_availability: "blocked",
        native_launch_available: false,
        protocols: ["openai_chat_completions"],
        provider_bridge: "ready",
        reason_ids: ["managed_agent_inactive"],
        schema_version: 1,
        status: "blocked",
      },
      registry_id: "future-acp",
      update_available: false,
      version: "2.0.0",
    };
    const markup = renderToStaticMarkup(
      <InstalledAgentCard
        agent={installed}
        busyAction={null}
        probe={null}
        onActivate={vi.fn()}
        onProbe={vi.fn()}
        onRemove={vi.fn()}
        onRollback={vi.fn()}
        onUpdate={vi.fn()}
        onUse={vi.fn()}
      />,
    );

    expect(markup).toContain("Installed, but not active yet");
    expect(markup).toContain("temporary private home");
    expect(markup).toContain("permits loopback only");
    expect(markup).toContain("perform an ACP initialize handshake");
    expect(markup).toContain("current active revision unchanged");
    expect(markup).toContain("Check &amp; activate");
    expect(markup).toContain('disabled="" type="button">Use in new run');
  });

  it("keeps a native-only agent usable without presenting it as an install failure", () => {
    const installed: InstalledAgentProjection = {
      activation_status: "ready",
      active: true,
      auth_required: false,
      distribution_kind: "npx",
      install_id: "install-native-only",
      local_agent_id: "amp-acp",
      probe_state: "ready",
      readiness: {
        acp_transport: "ready",
        action: "use_native",
        gateway_availability: "unsupported",
        native_launch_available: true,
        protocols: [],
        provider_bridge: "native-only",
        reason_ids: ["amp_acp_provider_configuration_unsupported"],
        schema_version: 1,
        status: "native-only",
      },
      registry_id: "amp-acp",
      update_available: false,
      version: "1.0.0",
    };
    const markup = renderToStaticMarkup(
      <InstalledAgentCard
        agent={installed}
        busyAction={null}
        probe={null}
        onActivate={vi.fn()}
        onProbe={vi.fn()}
        onRemove={vi.fn()}
        onRollback={vi.fn()}
        onUpdate={vi.fn()}
        onUse={vi.fn()}
      />,
    );

    expect(markup).toContain("Native launch only");
    expect(markup).toContain("The installation is healthy");
    expect(markup).toContain("native launch remains available");
    expect(markup).toContain("Use in new run");
    expect(markup).not.toContain('disabled="" type="button">Use in new run');
  });
});
