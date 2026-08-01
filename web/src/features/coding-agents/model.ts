import type {
  AgentIntegrity,
  AgentRegistryEntryProjection,
  InstalledAgentProjection,
  LocalAgentManifestProjection,
} from "../../api/agentRuntimes";

export type CodingAgentTab = "installed" | "registry" | "local";

export interface AgentMarketplaceFilters {
  platform: string;
  distribution: string;
  integrity: AgentIntegrity | "all";
  license: string;
}

export const emptyAgentMarketplaceFilters: AgentMarketplaceFilters = {
  distribution: "all",
  integrity: "all",
  license: "all",
  platform: "all",
};

export function filterRegistryAgents(
  entries: readonly AgentRegistryEntryProjection[],
  query: string,
  filters: AgentMarketplaceFilters,
): AgentRegistryEntryProjection[] {
  const needle = query.trim().toLocaleLowerCase();
  return entries.filter((entry) => {
    const matchesQuery = needle.length === 0 || [
      entry.registry_id,
      entry.name,
      entry.description,
      entry.license,
    ].some((value) => value.toLocaleLowerCase().includes(needle));
    return matchesQuery
      && (filters.platform === "all" || entry.platforms.includes(filters.platform))
      && (filters.distribution === "all" || entry.distribution_kinds.includes(filters.distribution as "binary" | "npx" | "uvx"))
      && (filters.integrity === "all" || entry.integrity === filters.integrity)
      && (filters.license === "all" || entry.license === filters.license);
  });
}

export function filterInstalledAgents(
  entries: readonly InstalledAgentProjection[],
  query: string,
): InstalledAgentProjection[] {
  const needle = query.trim().toLocaleLowerCase();
  if (needle.length === 0) return [...entries];
  return entries.filter((entry) =>
    [entry.local_agent_id, entry.registry_id, entry.version]
      .some((value) => value.toLocaleLowerCase().includes(needle)),
  );
}

export function filterLocalManifests(
  entries: readonly LocalAgentManifestProjection[],
  query: string,
): LocalAgentManifestProjection[] {
  const needle = query.trim().toLocaleLowerCase();
  if (needle.length === 0) return [...entries];
  return entries.filter((entry) =>
    [entry.agent_id, entry.display_name, entry.source]
      .some((value) => value.toLocaleLowerCase().includes(needle)),
  );
}

export function registryFilterOptions(
  entries: readonly AgentRegistryEntryProjection[],
): { platforms: string[]; licenses: string[] } {
  return {
    licenses: [...new Set(entries.map((entry) => entry.license))].toSorted(),
    platforms: [...new Set(entries.flatMap((entry) => entry.platforms))].toSorted(),
  };
}

export function marketplaceTabCounts(input: {
  installed: readonly InstalledAgentProjection[];
  registry: readonly AgentRegistryEntryProjection[];
  local: readonly LocalAgentManifestProjection[];
}): Record<CodingAgentTab, number> {
  return {
    installed: input.installed.length,
    local: input.local.length,
    registry: input.registry.length,
  };
}

export function integrityLabel(value: AgentIntegrity): string {
  if (value === "verified") return "Verified metadata";
  if (value === "unverified") return "Extra confirmation required";
  return "Mixed integrity";
}
