import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";

import {
  activateAgentRuntime,
  agentRuntimeKeys,
  cancelAgentInstallation,
  fetchAgentInstallationOperation,
  fetchAgentRuntimeInventory,
  observeAgentInstallation,
  prepareAgentRun,
  previewAgentInstall,
  probeAgentRuntime,
  recoverAgentInstallations,
  removeAgentRuntime,
  rollbackAgentRuntime,
  startAgentInstall,
  startAgentUpdate,
  type AgentInstallationOperationResponse,
  type AgentActivationResponse,
  type AgentProbeResponse,
  type AgentRegistryEntryProjection,
} from "../../api/agentRuntimes";
import { InstalledAgentCard, LocalManifestCard, RegistryAgentCard } from "./AgentCards";
import { AgentInstallDrawer } from "./InstallDrawer";
import { UpgradeRadarSection } from "./UpgradeRadarSection";
import {
  emptyAgentMarketplaceFilters,
  filterInstalledAgents,
  filterLocalManifests,
  filterRegistryAgents,
  marketplaceTabCounts,
  registryFilterOptions,
  type CodingAgentTab,
} from "./model";
import "./coding-agents.css";

const terminalOperationStates = new Set(["completed", "inactive", "canceled", "failed", "recovered"]);

type RuntimeAction =
  | { kind: "activate"; installId: string; localAgentId: string }
  | { kind: "probe"; installId: string; localAgentId: string }
  | { kind: "remove"; localAgentId: string }
  | { kind: "rollback"; localAgentId: string }
  | { kind: "use"; localAgentId: string };

export function CodingAgentsMarketplace() {
  const queryClient = useQueryClient();
  const inventoryQuery = useQuery({
    queryFn: ({ signal }) => fetchAgentRuntimeInventory({}, signal),
    queryKey: agentRuntimeKeys.inventory(),
  });
  const [activeTab, setActiveTab] = useState<CodingAgentTab>("installed");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [filters, setFilters] = useState(emptyAgentMarketplaceFilters);
  const [selectedAgent, setSelectedAgent] = useState<AgentRegistryEntryProjection | null>(null);
  const [alias, setAlias] = useState("");
  const [allowUnverified, setAllowUnverified] = useState(false);
  const [operation, setOperation] = useState<AgentInstallationOperationResponse | null>(null);
  const [probes, setProbes] = useState<Record<string, AgentProbeResponse>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const refreshMutation = useMutation({
    mutationFn: () => fetchAgentRuntimeInventory({ refresh: true }),
    onSuccess: (inventory) => {
      queryClient.setQueryData(agentRuntimeKeys.inventory(), inventory);
      setNotice(inventory.stale ? "Refresh kept the last valid cached revision." : "Registry cache refreshed.");
    },
  });
  const previewMutation = useMutation({
    mutationFn: () => {
      if (selectedAgent === null) throw new Error("Select a registry agent first");
      return previewAgentInstall(selectedAgent.registry_id, alias.trim() || null);
    },
  });
  const installMutation = useMutation({
    mutationFn: () => {
      if (selectedAgent === null) throw new Error("Select a registry agent first");
      const expectedPlanId = previewMutation.data?.plan?.plan_id;
      if (expectedPlanId === undefined) throw new Error("Review an install plan first");
      return startAgentInstall(
        selectedAgent.registry_id,
        alias.trim() || null,
        expectedPlanId,
        allowUnverified,
      );
    },
    onSuccess: setOperation,
  });
  const updateMutation = useMutation({
    mutationFn: (localAgentId: string) => startAgentUpdate(localAgentId, false),
    onSuccess: setOperation,
  });
  const cancelMutation = useMutation({
    mutationFn: (operationId: string) => cancelAgentInstallation(operationId),
    onSuccess: setOperation,
  });
  const recoveryMutation = useMutation({
    mutationFn: () => recoverAgentInstallations(),
    onSuccess: (result) => {
      setNotice(
        `Recovery checked ${result.recovered_plan_ids.length} staging plan(s) and ${result.recovered_operation_ids.length} interrupted operation(s).`,
      );
      void queryClient.invalidateQueries({ queryKey: agentRuntimeKeys.inventory() });
    },
  });
  const actionMutation = useMutation({
    mutationFn: runRuntimeAction,
    onError: (error, action) => {
      setNotice(
        action.kind === "activate"
          ? `Could not confirm activation of ${action.localAgentId}. Refresh the inventory before retrying. ${error.message}`
          : `${action.kind} failed for ${action.localAgentId}. ${error.message}`,
      );
    },
    onSuccess: (result, action) => {
      if (action.kind === "probe") {
        setProbes((current) => ({ ...current, [action.installId]: result as AgentProbeResponse }));
        return;
      }
      if (action.kind === "activate") {
        const activation = result as AgentActivationResponse;
        setProbes((current) => ({ ...current, [action.installId]: activation.probe }));
        setNotice(
          activation.active
            ? `${activation.local_agent_id} ${activation.version} passed the isolated ACP check and is now active.`
            : `${activation.local_agent_id} remains inactive after a fresh ACP check (${activation.probe.state}); the current active revision was not changed.`,
        );
        void queryClient.invalidateQueries({ queryKey: agentRuntimeKeys.inventory() });
        return;
      }
      if (action.kind === "use") {
        globalThis.location.assign((result as { href: string }).href);
        return;
      }
      setNotice(action.kind === "remove" ? "Managed artifacts removed." : "Previous managed revision restored.");
      void queryClient.invalidateQueries({ queryKey: agentRuntimeKeys.inventory() });
    },
  });

  useEffect(() => {
    if (operation === null || operation.terminal || typeof globalThis.EventSource !== "function") return;
    const lastSequence = operation.events.at(-1)?.sequence ?? -1;
    const refreshOperation = () => {
      void fetchAgentInstallationOperation(operation.operation_id).then((next) => {
        setOperation(next);
        if (next.terminal) {
          void queryClient.invalidateQueries({ queryKey: agentRuntimeKeys.inventory() });
        }
      });
    };
    return observeAgentInstallation(operation.operation_id, lastSequence, refreshOperation, refreshOperation);
  }, [operation, queryClient]);

  const inventory = inventoryQuery.data;
  const filterOptions = useMemo(
    () => registryFilterOptions(inventory?.registry_entries ?? []),
    [inventory?.registry_entries],
  );
  const visibleRegistry = useMemo(
    () => filterRegistryAgents(inventory?.registry_entries ?? [], deferredSearch, filters),
    [deferredSearch, filters, inventory?.registry_entries],
  );
  const visibleInstalled = useMemo(
    () => filterInstalledAgents(inventory?.installed ?? [], deferredSearch),
    [deferredSearch, inventory?.installed],
  );
  const visibleLocal = useMemo(
    () => filterLocalManifests(inventory?.local_manifests ?? [], deferredSearch),
    [deferredSearch, inventory?.local_manifests],
  );
  const counts = marketplaceTabCounts({
    installed: inventory?.installed ?? [],
    local: inventory?.local_manifests ?? [],
    registry: inventory?.registry_entries ?? [],
  });
  const busyAction = actionMutation.isPending
    ? runtimeActionKey(actionMutation.variables)
    : updateMutation.isPending
      ? `update:${updateMutation.variables}`
      : null;

  const closeDrawer = () => {
    setSelectedAgent(null);
    setAlias("");
    setAllowUnverified(false);
    previewMutation.reset();
    installMutation.reset();
    if (operation?.terminal === true) setOperation(null);
  };
  const chooseAgent = (agent: AgentRegistryEntryProjection) => {
    setSelectedAgent(agent);
    setAlias("");
    setAllowUnverified(false);
    previewMutation.reset();
    setOperation(null);
  };
  const changeAlias = (value: string) => {
    setAlias(value);
    previewMutation.reset();
  };

  return (
    <div className="coding-agent-marketplace">
      <header className="coding-agent-header">
        <div>
          <span className="section-kicker">Coding agents</span>
          <h1>Agent runtimes</h1>
          <p>Install official ACP entries into isolated managed roots, or inspect existing local manifests.</p>
        </div>
        <div className="registry-health-actions">
          {inventory === undefined ? null : <RegistryHealth inventory={inventory} />}
          <button disabled={refreshMutation.isPending} onClick={() => refreshMutation.mutate()} type="button">
            {refreshMutation.isPending ? "Refreshing…" : "Refresh registry"}
          </button>
          <button disabled={recoveryMutation.isPending} onClick={() => recoveryMutation.mutate()} type="button">
            Recover interrupted installs
          </button>
        </div>
      </header>
      {notice === null ? null : <div className="marketplace-notice" role="status">{notice}<button aria-label="Dismiss notice" onClick={() => setNotice(null)} type="button">×</button></div>}
      {inventory?.stale === true ? <div className="stale-registry-banner">Showing the last validated registry snapshot. Refresh failed or this cache is stale; installs stay pinned to this revision.</div> : null}
      {operation !== null && selectedAgent === null ? (
        <OperationBanner operation={operation} onCancel={() => cancelMutation.mutate(operation.operation_id)} />
      ) : null}
      <nav aria-label="Agent runtime sources" className="agent-source-tabs">
        {(["installed", "registry", "local"] as const).map((tab) => (
          <button aria-selected={activeTab === tab} className={activeTab === tab ? "active" : ""} key={tab} onClick={() => startTransition(() => setActiveTab(tab))} role="tab" type="button">
            {tab === "registry" ? "ACP Registry" : tab === "local" ? "Local manifests" : "Installed"}
            <span>{counts[tab]}</span>
          </button>
        ))}
      </nav>
      <section className="agent-marketplace-toolbar">
        <label className="agent-search-field"><span className="sr-only">Search agents</span><input onChange={(event) => setSearch(event.target.value)} placeholder={`Search ${activeTab === "registry" ? "the cached registry" : activeTab}`} type="search" value={search} /></label>
        {activeTab !== "registry" ? null : (
          <div className="agent-filter-row">
            <FilterSelect label="Platform" value={filters.platform} values={filterOptions.platforms} onChange={(platform) => setFilters((current) => ({ ...current, platform }))} />
            <FilterSelect label="Distribution" value={filters.distribution} values={["binary", "npx", "uvx"]} onChange={(distribution) => setFilters((current) => ({ ...current, distribution }))} />
            <FilterSelect label="Integrity" value={filters.integrity} values={["verified", "unverified", "mixed"]} onChange={(integrity) => setFilters((current) => ({ ...current, integrity: integrity as typeof current.integrity }))} />
            <FilterSelect label="License" value={filters.license} values={filterOptions.licenses} onChange={(license) => setFilters((current) => ({ ...current, license }))} />
          </div>
        )}
      </section>
      <main className="agent-card-grid" data-tab={activeTab}>
        {inventoryQuery.isPending ? <MarketplaceEmpty title="Loading agent inventory…" detail="Reading the validated local revision." />
          : inventoryQuery.isError ? <MarketplaceEmpty title="Agent inventory unavailable" detail="The backend did not return a validated revision." />
          : activeTab === "registry" ? visibleRegistry.map((agent) => <RegistryAgentCard agent={agent} key={agent.registry_id} onPreview={chooseAgent} />)
          : activeTab === "installed" ? visibleInstalled.map((agent) => (
            <InstalledAgentCard
              agent={agent}
              busyAction={busyAction}
              key={agent.install_id}
              probe={probes[agent.install_id] ?? null}
              onActivate={(localAgentId, installId) => actionMutation.mutate({ kind: "activate", installId, localAgentId })}
              onProbe={(localAgentId, installId) => actionMutation.mutate({ kind: "probe", installId, localAgentId })}
              onRemove={(localAgentId) => { if (globalThis.confirm("Remove only GigaLoom-managed agent artifacts?")) actionMutation.mutate({ kind: "remove", localAgentId }); }}
              onRollback={(localAgentId) => actionMutation.mutate({ kind: "rollback", localAgentId })}
              onUpdate={(localAgentId) => updateMutation.mutate(localAgentId)}
              onUse={(localAgentId) => actionMutation.mutate({ kind: "use", localAgentId })}
            />
          )) : visibleLocal.map((agent) => <LocalManifestCard agent={agent} key={agent.agent_id} />)}
        {!inventoryQuery.isPending && !inventoryQuery.isError && ((activeTab === "registry" && visibleRegistry.length === 0) || (activeTab === "installed" && visibleInstalled.length === 0) || (activeTab === "local" && visibleLocal.length === 0)) ? (
          <MarketplaceEmpty title="No agents match this view" detail={activeTab === "local" ? "Register an advanced manifest with giga agent add --manifest." : "Change the local search or filters."} />
        ) : null}
      </main>
      <UpgradeRadarSection />
      {selectedAgent === null ? null : (
        <AgentInstallDrawer
          agent={selectedAgent}
          alias={alias}
          allowUnverified={allowUnverified}
          installPending={installMutation.isPending}
          installError={installErrorMessage(installMutation.error)}
          operation={operation?.registry_or_local_id === selectedAgent.registry_id ? operation : null}
          preview={previewMutation.data ?? null}
          previewPending={previewMutation.isPending}
          onAliasChange={changeAlias}
          onAllowUnverifiedChange={setAllowUnverified}
          onCancel={() => { if (operation !== null) cancelMutation.mutate(operation.operation_id); }}
          onClose={closeDrawer}
          onConfirm={() => installMutation.mutate()}
          onPreview={() => previewMutation.mutate()}
        />
      )}
    </div>
  );
}

async function runRuntimeAction(action: RuntimeAction): Promise<unknown> {
  if (action.kind === "activate") return activateAgentRuntime(action.localAgentId, action.installId);
  if (action.kind === "probe") return probeAgentRuntime(action.localAgentId);
  if (action.kind === "remove") return removeAgentRuntime(action.localAgentId);
  if (action.kind === "rollback") return rollbackAgentRuntime(action.localAgentId);
  return prepareAgentRun(action.localAgentId);
}

function runtimeActionKey(action: RuntimeAction): string {
  const target = action.kind === "activate" || action.kind === "probe"
    ? action.installId
    : action.localAgentId;
  return `${action.kind}:${target}`;
}

function installErrorMessage(error: Error | null): string | null {
  if (error === null) return null;
  if (error.message === "managed_agent_network_isolation_required") {
    return "Install unavailable: this server has no admitted managed-agent network isolation authority.";
  }
  return error.message;
}

function RegistryHealth({ inventory }: { inventory: { fetched_at: string; offline: boolean; snapshot_digest: string; stale: boolean } }) {
  return (
    <div className={`registry-health ${inventory.stale ? "stale" : "fresh"}`}>
      <span>{inventory.offline ? "Offline cache" : inventory.stale ? "Stale cache" : "Validated cache"}</span>
      <small>{inventory.snapshot_digest.slice(0, 10)} · {new Date(inventory.fetched_at).toLocaleString()}</small>
    </div>
  );
}

function FilterSelect({ label, onChange, value, values }: { label: string; onChange: (value: string) => void; value: string; values: string[] }) {
  return <label><span>{label}</span><select onChange={(event) => onChange(event.target.value)} value={value}><option value="all">All</option>{values.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}

function OperationBanner({ operation, onCancel }: { operation: AgentInstallationOperationResponse; onCancel: () => void }) {
  const lastEvent = operation.events.at(-1);
  return (
    <div className={`marketplace-operation ${operation.terminal ? "terminal" : "running"}`} role="status">
      <div><span className="operation-pulse" /><strong>{operation.kind}: {operation.status}</strong><small>{lastEvent?.reason_code ?? operation.operation_id}</small></div>
      {operation.terminal ? null : <button className="danger-button" onClick={onCancel} type="button">Cancel safely</button>}
    </div>
  );
}

function MarketplaceEmpty({ detail, title }: { detail: string; title: string }) {
  return <div className="agent-empty-state"><span aria-hidden="true">⌁</span><strong>{title}</strong><p>{detail}</p></div>;
}

export function isTerminalAgentOperation(status: string): boolean {
  return terminalOperationStates.has(status);
}
