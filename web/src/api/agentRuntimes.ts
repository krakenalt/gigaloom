import { fetchCockpit, mutateCockpit } from "./core";

export type AgentDistributionKind = "binary" | "npx" | "uvx";
export type AgentIntegrity = "verified" | "unverified" | "mixed";

export interface AgentDistributionProjection {
  kind: AgentDistributionKind;
  platform: string;
  architecture: string;
  integrity: Exclude<AgentIntegrity, "mixed">;
  package_or_archive: string;
}

export interface AgentRegistryEntryProjection {
  registry_id: string;
  name: string;
  version: string;
  description: string;
  license: string;
  repository: string | null;
  website: string | null;
  entry_digest: string;
  platforms: string[];
  distribution_kinds: AgentDistributionKind[];
  integrity: AgentIntegrity;
  distributions: AgentDistributionProjection[];
}

export interface InstalledAgentProjection {
  local_agent_id: string;
  registry_id: string;
  version: string;
  install_id: string;
  distribution_kind: AgentDistributionKind;
  active: boolean;
  activation_status: string;
  probe_state: string;
  auth_required: boolean;
  update_available: boolean;
}

export interface LocalAgentManifestProjection {
  agent_id: string;
  display_name: string;
  profile_digest: string;
  source: string;
  structured_route_ids: string[];
  native_available: boolean;
}

export interface AgentRuntimeInventoryResponse {
  schema_version: 1;
  snapshot_digest: string;
  fetched_at: string;
  stale: boolean;
  offline: boolean;
  from_cache: boolean;
  refresh_error_code: string | null;
  explicitly_refreshed: boolean;
  registry_entries: AgentRegistryEntryProjection[];
  installed: InstalledAgentProjection[];
  local_manifests: LocalAgentManifestProjection[];
  install_decisions_browser_owned: false;
}

export interface AgentDistributionDecisionProjection {
  distribution_digest: string;
  rank: number;
  status: "selected" | "rejected";
  reason_code: string;
}

export interface AgentInstallPlanProjection {
  plan_id: string;
  registry_id: string;
  entry_digest: string;
  snapshot_digest: string;
  local_agent_id: string;
  version: string;
  platform: string;
  architecture: string;
  distribution_kind: AgentDistributionKind;
  package_or_archive: string;
  integrity_policy: string;
  lifecycle_script_policy: string;
  side_effects: string[];
  confirmation_required: true;
  expires_at: string;
}

export interface AgentInstallPreviewResponse {
  schema_version: 1;
  reason_code: string;
  local_agent_id: string | null;
  proposed_local_agent_id: string | null;
  collision_namespaces: string[];
  decisions: AgentDistributionDecisionProjection[];
  plan: AgentInstallPlanProjection | null;
  installation_started: false;
  browser_selected_distribution: false;
}

export interface AgentInstallationEventProjection {
  sequence: number;
  state: string;
  reason_code: string;
  observed_at: string;
}

export interface AgentInstallationOperationResponse {
  schema_version: 1;
  operation_id: string;
  kind: "install" | "update";
  registry_or_local_id: string;
  requested_local_agent_id: string | null;
  status: string;
  terminal: boolean;
  events: AgentInstallationEventProjection[];
  result_local_agent_id: string | null;
  result_install_id: string | null;
  result_version: string | null;
  result_active: boolean | null;
  terminal_reason_code: string | null;
  content_free: true;
}

export interface AgentProbeResponse {
  schema_version: 1;
  state: string;
  protocol_state: string;
  protocol_version: string | null;
  auth_methods: string[];
  capabilities: string[];
  losses: string[];
  warnings: string[];
  native_home_isolated: boolean;
  network_policy: string;
  content_free: true;
}

export interface AgentRecoveryResponse {
  schema_version: 1;
  recovered_operation_ids: string[];
  recovered_plan_ids: string[];
  cleanup_statuses: string[];
}

export interface AgentUseResponse {
  schema_version: 1;
  local_agent_id: string;
  href: string;
  run_started: false;
}

export const agentRuntimeKeys = {
  root: ["cockpit", "agent-runtimes"] as const,
  inventory: () => [...agentRuntimeKeys.root, "inventory"] as const,
  operation: (operationId: string) =>
    [...agentRuntimeKeys.root, "operation", operationId] as const,
};

export function fetchAgentRuntimeInventory(
  options: { refresh?: boolean } = {},
  signal?: AbortSignal,
): Promise<AgentRuntimeInventoryResponse> {
  const parameters = new URLSearchParams();
  if (options.refresh === true) parameters.set("refresh", "true");
  const query = parameters.size > 0 ? `?${parameters.toString()}` : "";
  return fetchCockpit<AgentRuntimeInventoryResponse>(
    `/api/agent-runtimes/inventory${query}`,
    signal,
  );
}

export function previewAgentInstall(
  registryQuery: string,
  localAgentId: string | null,
  signal?: AbortSignal,
): Promise<AgentInstallPreviewResponse> {
  return mutateCockpit<AgentInstallPreviewResponse>(
    "/api/agent-runtimes/installations/preview",
    { registry_query: registryQuery, local_agent_id: localAgentId },
    signal,
  );
}

export function startAgentInstall(
  registryQuery: string,
  localAgentId: string | null,
  expectedPlanId: string,
  allowUnverified: boolean,
  signal?: AbortSignal,
): Promise<AgentInstallationOperationResponse> {
  return mutateCockpit<AgentInstallationOperationResponse>(
    "/api/agent-runtimes/installations",
    {
      allow_unverified: allowUnverified,
      confirmed: true,
      expected_plan_id: expectedPlanId,
      local_agent_id: localAgentId,
      registry_query: registryQuery,
    },
    signal,
  );
}

export function fetchAgentInstallationOperation(
  operationId: string,
  signal?: AbortSignal,
): Promise<AgentInstallationOperationResponse> {
  return fetchCockpit<AgentInstallationOperationResponse>(
    `/api/agent-runtimes/installations/${encodeURIComponent(operationId)}`,
    signal,
  );
}

export function cancelAgentInstallation(
  operationId: string,
  signal?: AbortSignal,
): Promise<AgentInstallationOperationResponse> {
  return mutateCockpit<AgentInstallationOperationResponse>(
    `/api/agent-runtimes/installations/${encodeURIComponent(operationId)}/cancel`,
    undefined,
    signal,
  );
}

export function recoverAgentInstallations(
  signal?: AbortSignal,
): Promise<AgentRecoveryResponse> {
  return mutateCockpit<AgentRecoveryResponse>(
    "/api/agent-runtimes/installations/recover",
    undefined,
    signal,
  );
}

export function startAgentUpdate(
  localAgentId: string,
  allowUnverified: boolean,
  signal?: AbortSignal,
): Promise<AgentInstallationOperationResponse> {
  return mutateCockpit<AgentInstallationOperationResponse>(
    `/api/agent-runtimes/${encodeURIComponent(localAgentId)}/update`,
    { allow_unverified: allowUnverified, confirmed: true },
    signal,
  );
}

export function probeAgentRuntime(
  localAgentId: string,
  signal?: AbortSignal,
): Promise<AgentProbeResponse> {
  return mutateCockpit<AgentProbeResponse>(
    `/api/agent-runtimes/${encodeURIComponent(localAgentId)}/probe`,
    undefined,
    signal,
  );
}

export function rollbackAgentRuntime(
  localAgentId: string,
  signal?: AbortSignal,
): Promise<{ status: string }> {
  return mutateCockpit<{ status: string }>(
    `/api/agent-runtimes/${encodeURIComponent(localAgentId)}/rollback`,
    { confirmed: true },
    signal,
  );
}

export function removeAgentRuntime(
  localAgentId: string,
  signal?: AbortSignal,
): Promise<{ removed_install_count: number }> {
  return mutateCockpit<{ removed_install_count: number }>(
    `/api/agent-runtimes/${encodeURIComponent(localAgentId)}/remove`,
    { confirmed: true },
    signal,
  );
}

export function prepareAgentRun(
  localAgentId: string,
  signal?: AbortSignal,
): Promise<AgentUseResponse> {
  return fetchCockpit<AgentUseResponse>(
    `/api/agent-runtimes/${encodeURIComponent(localAgentId)}/use`,
    signal,
  );
}

export function observeAgentInstallation(
  operationId: string,
  afterSequence: number,
  onEvent: (event: AgentInstallationEventProjection) => void,
  onDisconnect?: () => void,
): () => void {
  const parameters = new URLSearchParams({ after: String(afterSequence) });
  const source = new EventSource(
    `/api/agent-runtimes/installations/${encodeURIComponent(operationId)}/events?${parameters}`,
  );
  source.addEventListener("progress", (message) => {
    onEvent(JSON.parse((message as MessageEvent<string>).data) as AgentInstallationEventProjection);
  });
  source.onerror = () => {
    source.close();
    onDisconnect?.();
  };
  return () => source.close();
}
