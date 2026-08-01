import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  AgentInstallPreviewResponse,
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
  license: "MIT",
  name: "Generic coding agent",
  platforms: ["darwin-aarch64"],
  registry_id: "generic-agent",
  repository: "https://example.test/source",
  version: "1.0.0",
  website: null,
};

describe("coding-agent marketplace components", () => {
  it("shows registry facts and asks the backend for a preview", () => {
    const markup = renderToStaticMarkup(<RegistryAgentCard agent={agent} onPreview={vi.fn()} />);

    expect(markup).toContain("Verified metadata");
    expect(markup).toContain("darwin-aarch64");
    expect(markup).toContain("Review install");
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

  it("exposes probe, auth, update, rollback, remove, and use actions", () => {
    const installed: InstalledAgentProjection = {
      activation_status: "degraded",
      active: true,
      auth_required: true,
      distribution_kind: "npx",
      install_id: "install-generic",
      local_agent_id: "generic-agent",
      probe_state: "auth_required",
      registry_id: "generic-agent",
      update_available: true,
      version: "1.0.0",
    };
    const markup = renderToStaticMarkup(
      <InstalledAgentCard
        agent={installed}
        busyAction={null}
        probe={null}
        onProbe={vi.fn()}
        onRemove={vi.fn()}
        onRollback={vi.fn()}
        onUpdate={vi.fn()}
        onUse={vi.fn()}
      />,
    );

    for (const label of ["Probe", "Update", "Rollback", "Remove", "Use in new run"]) {
      expect(markup).toContain(label);
    }
    expect(markup).toContain("Authentication");
    expect(markup).toContain("Required");
  });
});
