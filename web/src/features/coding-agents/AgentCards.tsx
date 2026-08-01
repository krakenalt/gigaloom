import type {
  AgentProbeResponse,
  AgentRegistryEntryProjection,
  InstalledAgentProjection,
  LocalAgentManifestProjection,
} from "../../api/agentRuntimes";
import { integrityLabel } from "./model";

export function RegistryAgentCard({
  agent,
  onPreview,
}: {
  agent: AgentRegistryEntryProjection;
  onPreview: (agent: AgentRegistryEntryProjection) => void;
}) {
  return (
    <article className="coding-agent-card registry-card">
      <div className="agent-card-heading">
        <AgentGlyph label={agent.name} />
        <div>
          <span className="agent-card-id">{agent.registry_id}</span>
          <h3>{agent.name}</h3>
        </div>
        <span className={`integrity-badge ${agent.integrity}`}>{integrityLabel(agent.integrity)}</span>
      </div>
      <p>{agent.description}</p>
      <dl className="agent-card-facts">
        <div><dt>Version</dt><dd>{agent.version}</dd></div>
        <div><dt>License</dt><dd>{agent.license || "Not declared"}</dd></div>
        <div><dt>Platforms</dt><dd>{agent.platforms.join(", ")}</dd></div>
        <div><dt>Distributions</dt><dd>{agent.distribution_kinds.join(" · ")}</dd></div>
      </dl>
      <div className="distribution-strip" aria-label="Available distributions">
        {agent.distributions.map((distribution) => (
          <span key={`${distribution.kind}-${distribution.platform}-${distribution.architecture}`}>
            {distribution.kind} · {distribution.platform}-{distribution.architecture} · {distribution.integrity}
          </span>
        ))}
      </div>
      <div className="agent-card-actions">
        {agent.repository === null ? null : <a href={agent.repository} rel="noreferrer" target="_blank">Source</a>}
        <button className="primary-button" onClick={() => onPreview(agent)} type="button">
          Review install
        </button>
      </div>
    </article>
  );
}

export function InstalledAgentCard({
  agent,
  busyAction,
  probe,
  onProbe,
  onRemove,
  onRollback,
  onUpdate,
  onUse,
}: {
  agent: InstalledAgentProjection;
  busyAction: string | null;
  probe: AgentProbeResponse | null;
  onProbe: (id: string) => void;
  onRemove: (id: string) => void;
  onRollback: (id: string) => void;
  onUpdate: (id: string) => void;
  onUse: (id: string) => void;
}) {
  const disabled = busyAction !== null;
  return (
    <article className={`coding-agent-card installed-card ${agent.active ? "active" : "inactive"}`}>
      <div className="agent-card-heading">
        <AgentGlyph label={agent.local_agent_id} />
        <div>
          <span className="agent-card-id">{agent.registry_id}</span>
          <h3>{agent.local_agent_id}</h3>
        </div>
        <span className={`runtime-state ${agent.activation_status}`}>{agent.activation_status}</span>
      </div>
      <dl className="agent-card-facts compact">
        <div><dt>Version</dt><dd>{agent.version}</dd></div>
        <div><dt>Distribution</dt><dd>{agent.distribution_kind}</dd></div>
        <div><dt>ACP probe</dt><dd>{agent.probe_state}</dd></div>
        <div><dt>Authentication</dt><dd>{agent.auth_required ? "Required" : "Not requested"}</dd></div>
      </dl>
      {agent.update_available ? <div className="agent-update-notice">A newer registry version is available. Existing files stay unchanged.</div> : null}
      {probe === null ? null : (
        <div className="probe-result" role="status">
          <strong>Probe: {probe.state}</strong>
          <span>{probe.auth_methods.length > 0 ? `Auth: ${probe.auth_methods.join(", ")}` : "No auth method requested"}</span>
          {probe.losses.length > 0 ? <span>Losses: {probe.losses.join(", ")}</span> : null}
          {probe.warnings.length > 0 ? <span>Warnings: {probe.warnings.join(", ")}</span> : null}
        </div>
      )}
      <div className="agent-card-actions wrap">
        <button disabled={disabled} onClick={() => onProbe(agent.local_agent_id)} type="button">Probe</button>
        {agent.update_available ? <button disabled={disabled} onClick={() => onUpdate(agent.local_agent_id)} type="button">Update</button> : null}
        <button disabled={disabled} onClick={() => onRollback(agent.local_agent_id)} type="button">Rollback</button>
        <button className="danger-button" disabled={disabled} onClick={() => onRemove(agent.local_agent_id)} type="button">Remove</button>
        <button className="primary-button" disabled={disabled || !agent.active} onClick={() => onUse(agent.local_agent_id)} type="button">Use in new run</button>
      </div>
    </article>
  );
}

export function LocalManifestCard({ agent }: { agent: LocalAgentManifestProjection }) {
  return (
    <article className="coding-agent-card local-card">
      <div className="agent-card-heading">
        <AgentGlyph label={agent.display_name} />
        <div><span className="agent-card-id">{agent.agent_id}</span><h3>{agent.display_name}</h3></div>
        <span className="runtime-state local">Local manifest</span>
      </div>
      <dl className="agent-card-facts compact">
        <div><dt>Source</dt><dd>{agent.source}</dd></div>
        <div><dt>Native route</dt><dd>{agent.native_available ? "Declared" : "Not declared"}</dd></div>
        <div><dt>Structured routes</dt><dd>{agent.structured_route_ids.join(", ") || "None"}</dd></div>
        <div><dt>Profile digest</dt><dd className="digest-value">{agent.profile_digest.slice(0, 12)}</dd></div>
      </dl>
    </article>
  );
}

function AgentGlyph({ label }: { label: string }) {
  const initials = label
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toLocaleUpperCase())
    .join("") || "A";
  return <span aria-hidden="true" className="agent-glyph">{initials}</span>;
}
