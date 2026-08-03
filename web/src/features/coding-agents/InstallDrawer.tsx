import type {
  AgentInstallationEventProjection,
  AgentInstallPreviewResponse,
  AgentInstallPlanProjection,
  AgentInstallationOperationResponse,
  AgentRegistryEntryProjection,
} from "../../api/agentRuntimes";

type InstallPhaseState = "complete" | "current" | "upcoming" | "failed" | "canceled" | "attention";

interface InstallPhase {
  title: string;
  detail: string;
}

type InstallPhases = [InstallPhase, InstallPhase, InstallPhase, InstallPhase];

const failureCopy: Record<string, { title: string; detail: string }> = {
  binary_digest_mismatch: {
    title: "Integrity check failed",
    detail: "The downloaded archive did not match the Registry checksum, so it was discarded and nothing was activated.",
  },
  binary_download_failed: {
    title: "Download failed",
    detail: "GigaLoom could not reach the approved download source. No agent was activated; check network or proxy access and try again.",
  },
  binary_download_redirect_rejected: {
    title: "Download was redirected somewhere unexpected",
    detail: "GigaLoom blocked an origin that was not part of the reviewed plan. Nothing was downloaded from that location.",
  },
  managed_agent_network_isolation_required: {
    title: "Safe compatibility check is unavailable",
    detail: "This server cannot run the required network-isolated ACP check, so installation stopped before activation.",
  },
};

export function AgentInstallDrawer({
  agent,
  alias,
  allowUnverified,
  installPending,
  installError,
  operation,
  preview,
  previewPending,
  onAliasChange,
  onAllowUnverifiedChange,
  onCancel,
  onClose,
  onConfirm,
  onPreview,
}: {
  agent: AgentRegistryEntryProjection;
  alias: string;
  allowUnverified: boolean;
  installPending: boolean;
  installError: string | null;
  operation: AgentInstallationOperationResponse | null;
  preview: AgentInstallPreviewResponse | null;
  previewPending: boolean;
  onAliasChange: (value: string) => void;
  onAllowUnverifiedChange: (value: boolean) => void;
  onCancel: () => void;
  onClose: () => void;
  onConfirm: () => void;
  onPreview: () => void;
}) {
  const plan = preview?.plan ?? null;
  const collision = preview?.proposed_local_agent_id ?? null;
  const terminal = operation?.terminal ?? false;
  const phases = installPhases(agent, plan);
  const currentPhase = operation === null ? 0 : operationPhaseIndex(operation.events);
  const statusCopy = operation === null
    ? null
    : operationStatusCopy(operation, phases[currentPhase] ?? phases[0]);
  return (
    <div className="agent-drawer-backdrop" role="presentation">
      <section aria-labelledby="agent-install-title" aria-modal="true" className="agent-install-drawer" role="dialog">
        <header>
          <div>
            <span className="section-kicker">{operation === null ? "Server-owned install plan" : "Managed agent installation"}</span>
            <h2 id="agent-install-title">{operation === null ? `Install ${agent.name}` : operationTitle(operation, agent.name)}</h2>
            <p>
              {operation === null
                ? "Review the exact platform choice and side effects before one confirmed transaction."
                : `GigaLoom installs ${agent.name} only inside its private managed directory. Your PATH and global packages stay unchanged.`}
            </p>
          </div>
          <button aria-label="Close install details" className="drawer-close" onClick={onClose} type="button">×</button>
        </header>
        {operation === null ? (
          <>
            <label className="agent-alias-field">
              <span>Local agent ID</span>
              <input onChange={(event) => onAliasChange(event.target.value)} placeholder={agent.registry_id} value={alias} />
              <small>Leave blank to use the registry ID. Existing native and core names are never shadowed.</small>
            </label>
            <button disabled={previewPending} onClick={onPreview} type="button">
              {previewPending ? "Resolving…" : "Generate install preview"}
            </button>
            {preview === null ? null : (
              <div className="install-preview" data-state={plan === null ? "blocked" : "ready"}>
                <div className="preview-heading">
                  <strong>{plan === null ? "Action required" : "Plan ready"}</strong>
                  <span>{preview.reason_code}</span>
                </div>
                {collision === null ? null : (
                  <div className="collision-choice">
                    <p>This ID belongs to: {preview.collision_namespaces.join(", ")}.</p>
                    <button onClick={() => onAliasChange(collision)} type="button">Use safe alias {collision}</button>
                  </div>
                )}
                {plan === null ? null : (
                  <>
                    <dl className="install-plan-facts">
                      <div><dt>Local ID</dt><dd>{plan.local_agent_id}</dd></div>
                      <div><dt>Version</dt><dd>{plan.version}</dd></div>
                      <div><dt>Target</dt><dd>{plan.platform}-{plan.architecture}</dd></div>
                      <div><dt>Distribution</dt><dd>{plan.distribution_kind}</dd></div>
                      <div><dt>Integrity</dt><dd>{plan.integrity_policy}</dd></div>
                      <div><dt>Scripts</dt><dd>{plan.lifecycle_script_policy}</dd></div>
                    </dl>
                    <div className="side-effect-list">
                      <span>Declared effects</span>
                      {plan.side_effects.map((effect) => <code key={effect}>{effect}</code>)}
                    </div>
                    {plan.integrity_policy === "allow_explicit_unverified" ? (
                      <label className="unverified-admission">
                        <input checked={allowUnverified} onChange={(event) => onAllowUnverifiedChange(event.target.checked)} type="checkbox" />
                        <span>I accept that this publisher did not supply an artifact checksum.</span>
                      </label>
                    ) : null}
                    <button
                      className="primary-button confirm-install"
                      disabled={installPending || (plan.integrity_policy === "allow_explicit_unverified" && !allowUnverified)}
                      onClick={onConfirm}
                      type="button"
                    >
                      {installPending ? "Starting transaction…" : "Confirm one transaction"}
                    </button>
                  </>
                )}
              </div>
            )}
            {installError === null ? null : (
              <p className="agent-install-error" role="alert">{installError}</p>
            )}
          </>
        ) : (
          <div className="agent-operation-progress" aria-live="polite" data-status={operation.status}>
            <div className="operation-status-card" role="status">
              <span className={`operation-pulse ${terminal ? "terminal" : ""}`} />
              <div>
                <span className="operation-step-label">{statusCopy?.label}</span>
                <strong>{statusCopy?.title}</strong>
                <p>{statusCopy?.detail}</p>
              </div>
            </div>
            <div className="install-boundary-note">
              <strong>What this installation changes</strong>
              <p>Files are created under <code>GIGALOOM_DATA_DIR</code>. Global npm/Python packages, <code>PATH</code>, and provider credentials are not modified.</p>
            </div>
            <section className="install-phase-section" aria-labelledby="install-phase-title">
              <div className="install-phase-heading">
                <h3 id="install-phase-title">Installation flow</h3>
                <span>{phaseProgressLabel(operation.status, currentPhase)}</span>
              </div>
              <ol className="install-phase-list">
                {phases.map((phase, index) => {
                  const phaseState = installPhaseState(index, currentPhase, operation.status);
                  return (
                    <li data-state={phaseState} key={phase.title}>
                      <span className="install-phase-marker" aria-hidden="true">{phaseState === "complete" ? "✓" : index + 1}</span>
                      <div><strong>{phase.title}</strong><small>{phase.detail}</small></div>
                      <span className="install-phase-state">{installPhaseStateLabel(phaseState)}</span>
                    </li>
                  );
                })}
              </ol>
            </section>
            <details className="operation-technical-details">
              <summary>Technical details</summary>
              <dl>
                <div><dt>Operation</dt><dd><code>{operation.operation_id}</code></dd></div>
                <div><dt>Version</dt><dd>{plan?.version ?? agent.version}</dd></div>
                <div><dt>Target</dt><dd>{plan === null ? agent.platforms.join(", ") : `${plan.platform}-${plan.architecture}`}</dd></div>
                <div><dt>Package</dt><dd>{plan === null ? agent.distribution_kinds.join(", ") : distributionLabel(plan.distribution_kind)}</dd></div>
              </dl>
              <ol className="operation-event-log">
                {operation.events.map((event) => (
                  <li key={event.sequence}>
                    <span>{event.sequence + 1}</span>
                    <code>{event.state}</code>
                    <code>{event.reason_code}</code>
                  </li>
                ))}
              </ol>
            </details>
            <div className="operation-actions">
              {terminal ? (
                <button className="primary-button" onClick={onClose} type="button">{operation.status === "completed" ? "Done" : "Close"}</button>
              ) : (
                <>
                  <button className="danger-button" onClick={onCancel} type="button">Cancel installation</button>
                  <small>Cancellation stops at the next safe checkpoint and cleans temporary files automatically.</small>
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function installPhases(
  agent: AgentRegistryEntryProjection,
  plan: AgentInstallPlanProjection | null,
): InstallPhases {
  const version = plan?.version ?? agent.version;
  const target = plan === null ? agent.platforms.join(", ") : `${plan.platform}-${plan.architecture}`;
  const binaryIntegrity = plan?.integrity_policy === "allow_explicit_unverified"
    ? "compute a local content digest"
    : "check the Registry SHA-256";
  const installDetail = plan?.distribution_kind === "npx"
    ? "Resolve the exact npm package with lifecycle scripts disabled, then install it into a private prefix."
    : plan?.distribution_kind === "uvx"
      ? "Resolve the exact Python package and install it into a private uv environment."
      : `Download from approved HTTPS origins, ${binaryIntegrity}, and unpack into a private managed directory.`;
  const installTitle = plan?.distribution_kind === "npx"
    ? "Install the npm package privately"
    : plan?.distribution_kind === "uvx"
      ? "Install the Python package privately"
      : "Download, verify, and unpack";
  return [
    {
      title: "Prepare the exact package",
      detail: `Lock ${agent.name} ${version} for ${target}; the browser cannot substitute another build.`,
    },
    {
      title: installTitle,
      detail: installDetail,
    },
    {
      title: "Run a safe compatibility check",
      detail: "Start one initialize-only ACP check with external network blocked and local loopback available before the agent can be activated.",
    },
    {
      title: "Activate for GigaLoom",
      detail: "Make this managed revision available only after the compatibility check passes.",
    },
  ];
}

function phaseProgressLabel(operationStatus: string, currentPhase: number): string {
  const step = currentPhase + 1;
  if (operationStatus === "completed") return "4 of 4 complete";
  if (operationStatus === "failed") return `Stopped at step ${step} of 4`;
  if (operationStatus === "canceled") return `Canceled at step ${step} of 4`;
  if (operationStatus === "inactive" || operationStatus === "retained_inactive") {
    return "Step 4 needs attention";
  }
  return `Step ${step} of 4`;
}

function operationPhaseIndex(events: AgentInstallationEventProjection[]): number {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const state = events[index]?.state;
    if (state === "activated" || state === "retained_inactive" || state === "completed" || state === "inactive") return 3;
    if (state === "probing") return 2;
    if (state === "installing") return 1;
    if (state === "queued" || state === "resolving" || state === "planned" || state === "recovered") return 0;
  }
  return 0;
}

function installPhaseState(
  index: number,
  currentPhase: number,
  operationStatus: string,
): InstallPhaseState {
  if (operationStatus === "completed") return "complete";
  if (index < currentPhase) return "complete";
  if (index > currentPhase) return "upcoming";
  if (operationStatus === "failed") return "failed";
  if (operationStatus === "canceled") return "canceled";
  if (operationStatus === "inactive" || operationStatus === "retained_inactive") return "attention";
  return "current";
}

function installPhaseStateLabel(state: InstallPhaseState): string {
  if (state === "complete") return "Complete";
  if (state === "current") return "In progress";
  if (state === "failed") return "Stopped";
  if (state === "canceled") return "Canceled";
  if (state === "attention") return "Needs attention";
  return "Next";
}

function operationStatusCopy(
  operation: AgentInstallationOperationResponse,
  currentPhase: InstallPhase,
): { label: string; title: string; detail: string } {
  if (operation.status === "completed") {
    return {
      label: "Completed",
      title: "Installed and ready to use",
      detail: "The exact package passed its integrity and ACP compatibility checks and is now active.",
    };
  }
  if (operation.status === "failed") {
    const reason = operation.terminal_reason_code ?? operation.events.at(-1)?.reason_code ?? "";
    return {
      label: "Installation stopped",
      ...(failureCopy[reason] ?? {
        title: "Installation could not finish",
        detail: "GigaLoom stopped before activation and cleaned temporary files. Open Technical details for the diagnostic code.",
      }),
    };
  }
  if (operation.status === "canceled") {
    return {
      label: "Canceled",
      title: "Installation canceled safely",
      detail: "The operation stopped at a safe checkpoint, temporary files were cleaned, and no agent was activated.",
    };
  }
  if (operation.status === "inactive" || operation.status === "retained_inactive") {
    return {
      label: "Needs attention",
      title: "Installed, but not activated",
      detail: "The files were installed, but the compatibility check did not approve activation. Existing agents were left unchanged.",
    };
  }
  return {
    label: `Step ${operationPhaseIndex(operation.events) + 1} of 4`,
    title: currentPhase.title,
    detail: currentPhase.detail,
  };
}

function operationTitle(operation: AgentInstallationOperationResponse, agentName: string): string {
  if (operation.status === "completed") return `${agentName} is ready`;
  if (operation.status === "failed") return `Could not install ${agentName}`;
  if (operation.status === "canceled") return `Installation canceled`;
  if (operation.status === "inactive" || operation.status === "retained_inactive") return `${agentName} needs attention`;
  return `Installing ${agentName}`;
}

function distributionLabel(value: AgentInstallPlanProjection["distribution_kind"]): string {
  if (value === "binary") return "Standalone binary";
  if (value === "npx") return "Private npm environment";
  return "Private Python environment";
}
