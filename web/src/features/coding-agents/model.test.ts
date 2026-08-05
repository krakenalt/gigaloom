import { describe, expect, it } from "vitest";

import type {
  AgentRegistryEntryProjection,
  InstalledAgentProjection,
  LocalAgentManifestProjection,
} from "../../api/agentRuntimes";
import {
  emptyAgentMarketplaceFilters,
  filterInstalledAgents,
  filterLocalManifests,
  filterRegistryAgents,
  marketplaceTabCounts,
  registryFilterOptions,
} from "./model";

const registry: AgentRegistryEntryProjection[] = [
  entry("verified-binary", "Verified Binary", ["darwin-aarch64"], ["binary"], "verified", "MIT"),
  entry("package-agent", "Package Agent", ["portable-any"], ["npx", "uvx"], "mixed", "Apache-2.0"),
];

describe("coding-agent marketplace model", () => {
  it("filters only the already fetched immutable registry revision", () => {
    expect(filterRegistryAgents(registry, "package", emptyAgentMarketplaceFilters).map((item) => item.registry_id)).toEqual([
      "package-agent",
    ]);
    expect(filterRegistryAgents(registry, "", {
      ...emptyAgentMarketplaceFilters,
      distribution: "binary",
      integrity: "verified",
      platform: "darwin-aarch64",
    }).map((item) => item.registry_id)).toEqual(["verified-binary"]);
    expect(filterRegistryAgents(registry, "", {
      ...emptyAgentMarketplaceFilters,
      license: "Apache-2.0",
    }).map((item) => item.registry_id)).toEqual(["package-agent"]);
  });

  it("derives stable local filter choices and source counts", () => {
    const installed: InstalledAgentProjection[] = [{
      activation_status: "ready",
      active: true,
      auth_required: false,
      distribution_kind: "binary",
      install_id: "install-1",
      local_agent_id: "verified-binary",
      probe_state: "ready",
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
      registry_id: "verified-binary",
      update_available: false,
      version: "1.0.0",
    }];
    const local: LocalAgentManifestProjection[] = [{
      agent_id: "local-reviewer",
      display_name: "Local Reviewer",
      native_available: false,
      profile_digest: "0".repeat(64),
      source: "agent.toml",
      structured_route_ids: ["review"],
    }];

    expect(registryFilterOptions(registry)).toEqual({
      licenses: ["Apache-2.0", "MIT"],
      platforms: ["darwin-aarch64", "portable-any"],
    });
    expect(marketplaceTabCounts({ installed, local, registry })).toEqual({
      installed: 1,
      local: 1,
      registry: 2,
    });
    expect(filterInstalledAgents(installed, "verified")).toHaveLength(1);
    expect(filterLocalManifests(local, "reviewer")).toHaveLength(1);
  });
});

function entry(
  registryId: string,
  name: string,
  platforms: string[],
  distributionKinds: Array<"binary" | "npx" | "uvx">,
  integrity: "verified" | "unverified" | "mixed",
  license: string,
): AgentRegistryEntryProjection {
  return {
    description: `${name} fixture`,
    distribution_kinds: distributionKinds,
    distributions: distributionKinds.map((kind) => ({
      architecture: "any",
      integrity: kind === "binary" ? "verified" : "unverified",
      kind,
      package_or_archive: `${registryId}.zip`,
      platform: "portable",
    })),
    entry_digest: "1".repeat(64),
    icon_ref: null,
    integrity,
    license,
    name,
    platforms,
    registry_id: registryId,
    repository: null,
    version: "1.0.0",
    website: null,
  };
}
