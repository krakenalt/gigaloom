import type {
  AgentInstallPreviewResponse,
  AgentInstallationOperationResponse,
  AgentRegistryEntryProjection,
} from "../../api/agentRuntimes";

export function AgentInstallDrawer({
  agent,
  alias,
  allowUnverified,
  installPending,
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
  return (
    <div className="agent-drawer-backdrop" role="presentation">
      <section aria-labelledby="agent-install-title" aria-modal="true" className="agent-install-drawer" role="dialog">
        <header>
          <div>
            <span className="section-kicker">Server-owned install plan</span>
            <h2 id="agent-install-title">Install {agent.name}</h2>
            <p>Review the exact platform choice and side effects before one confirmed transaction.</p>
          </div>
          <button aria-label="Close install review" className="drawer-close" onClick={onClose} type="button">×</button>
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
          </>
        ) : (
          <div className="agent-operation-progress" aria-live="polite">
            <div className="operation-status-line">
              <span className={`operation-pulse ${terminal ? "terminal" : ""}`} />
              <div><strong>{operation.status}</strong><span>Operation {operation.operation_id}</span></div>
            </div>
            <ol>
              {operation.events.map((event) => (
                <li key={event.sequence}>
                  <span>{event.sequence + 1}</span>
                  <div><strong>{event.state}</strong><small>{event.reason_code}</small></div>
                </li>
              ))}
            </ol>
            {terminal ? (
              <button className="primary-button" onClick={onClose} type="button">Done</button>
            ) : (
              <button className="danger-button" onClick={onCancel} type="button">Cancel safely</button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
