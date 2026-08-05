import { useState } from "react";

import type {
  AgentProbeResponse,
  AgentRegistryEntryProjection,
  AgentRuntimeReadinessProjection,
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
        <AgentGlyph
          iconRef={agent.icon_ref}
          key={agent.icon_ref ?? agent.registry_id}
          label={agent.name}
        />
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
  onActivate,
  onProbe,
  onRemove,
  onRollback,
  onUpdate,
  onUse,
}: {
  agent: InstalledAgentProjection;
  busyAction: string | null;
  probe: AgentProbeResponse | null;
  onActivate: (id: string, installId: string) => void;
  onProbe: (id: string, installId: string) => void;
  onRemove: (id: string) => void;
  onRollback: (id: string) => void;
  onUpdate: (id: string) => void;
  onUse: (id: string) => void;
}) {
  const disabled = busyAction !== null;
  const readiness = probe?.readiness ?? agent.readiness;
  return (
    <article className={`coding-agent-card installed-card ${agent.active ? "active" : "inactive"}`}>
      <div className="agent-card-heading">
        <AgentGlyph label={agent.local_agent_id} />
        <div>
          <span className="agent-card-id">{agent.registry_id}</span>
          <h3>{agent.local_agent_id}</h3>
        </div>
        <span className={`runtime-state ${readiness.status}`}>{readiness.status}</span>
      </div>
      <dl className="agent-card-facts compact">
        <div><dt>Version</dt><dd>{agent.version}</dd></div>
        <div><dt>Distribution</dt><dd>{agent.distribution_kind}</dd></div>
        <div><dt>ACP transport</dt><dd>{readiness.acp_transport}</dd></div>
        <div><dt>Provider bridge</dt><dd>{readiness.provider_bridge}</dd></div>
        <div><dt>Protocols</dt><dd>{readiness.protocols.join(", ") || "Not confirmed"}</dd></div>
        <div><dt>Gateway</dt><dd>{readiness.gateway_availability}</dd></div>
        <div><dt>Authentication</dt><dd>{agent.auth_required ? "Required" : "Not requested"}</dd></div>
      </dl>
      {agent.active ? null : (
        <div className="agent-activation-guide">
          <strong>Installed, but not active yet</strong>
          <p>Activation runs the exact managed command in a temporary private home, permits loopback only, and blocks external network access during the ACP check.</p>
          <ol>
            <li>Start the retained revision and perform an ACP initialize handshake.</li>
            <li>Check the protocol and required capabilities.</li>
            <li>Select it atomically only if compatible; otherwise keep the current active revision unchanged.</li>
          </ol>
        </div>
      )}
      <AgentReadinessGuide readiness={readiness} />
      {agent.update_available ? <div className="agent-update-notice">A newer registry version is available. Existing files stay unchanged.</div> : null}
      {probe === null ? null : (
        <div className="probe-result" role="status">
          <strong>Fresh probe: {probe.state}</strong>
          <span>Readiness is now {probe.readiness.status} for this view.</span>
          <details>
            <summary>Probe diagnostics</summary>
            <span>{probe.auth_methods.length > 0 ? `Auth: ${probe.auth_methods.join(", ")}` : "No auth method requested"}</span>
            {probe.losses.length > 0 ? <span>Losses: {probe.losses.join(", ")}</span> : null}
            {probe.warnings.length > 0 ? <span>Warnings: {probe.warnings.join(", ")}</span> : null}
          </details>
        </div>
      )}
      <div className="agent-card-actions wrap">
        {agent.active ? <button disabled={disabled} onClick={() => onProbe(agent.local_agent_id, agent.install_id)} type="button">Reprobe</button> : null}
        {agent.active ? null : (
          <button className="primary-button" disabled={disabled} onClick={() => onActivate(agent.local_agent_id, agent.install_id)} type="button">
            {busyAction === `activate:${agent.install_id}` ? "Checking & activating…" : "Check & activate"}
          </button>
        )}
        {agent.update_available ? <button disabled={disabled} onClick={() => onUpdate(agent.local_agent_id)} type="button">Update</button> : null}
        {agent.active ? <button disabled={disabled} onClick={() => onRollback(agent.local_agent_id)} type="button">Rollback</button> : null}
        <button className="danger-button" disabled={disabled} onClick={() => onRemove(agent.local_agent_id)} type="button">Remove</button>
        <button className="primary-button" disabled={disabled || !agent.active} onClick={() => onUse(agent.local_agent_id)} type="button">Use in new run</button>
      </div>
    </article>
  );
}

function AgentReadinessGuide({
  readiness,
}: {
  readiness: AgentRuntimeReadinessProjection;
}) {
  const copy = readinessCopy(readiness);
  return (
    <section className="agent-readiness-guide" data-status={readiness.status}>
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
      <small>{copy.action}</small>
      <details>
        <summary>Technical details</summary>
        <dl>
          <div><dt>Projection</dt><dd>v{readiness.schema_version}</dd></div>
          <div><dt>Action</dt><dd>{readiness.action}</dd></div>
          <div><dt>Native launch</dt><dd>{readiness.native_launch_available ? "available" : "blocked"}</dd></div>
          <div><dt>Reason IDs</dt><dd>{readiness.reason_ids.join(", ") || "none"}</dd></div>
        </dl>
      </details>
    </section>
  );
}

function readinessCopy(readiness: AgentRuntimeReadinessProjection): {
  action: string;
  detail: string;
  title: string;
} {
  if (readiness.status === "ready") {
    return {
      action: "Choose Use in new run, then select and preflight an exact gateway model.",
      detail: "ACP transport and the provider bridge are ready. A gateway selection replaces the provider default; GigaLoom does not fall back silently.",
      title: "Gateway routes are available",
    };
  }
  if (readiness.status === "native-only") {
    return {
      action: "Use the agent normally without selecting a gateway model.",
      detail: "ACP transport works, but this agent has no reviewed provider bridge. The installation is healthy and native launch remains available.",
      title: "Native launch only",
    };
  }
  if (readiness.status === "reprobe") {
    return {
      action: "Run Reprobe to refresh the content-free capability evidence.",
      detail: "The stored record predates the provider bridge check, so gateway availability is not inferred from the ACP transport.",
      title: "Provider bridge needs a fresh probe",
    };
  }
  return {
    action: readiness.native_launch_available
      ? "Native launch remains available; inspect diagnostics before choosing a gateway."
      : "Activate or reprobe this revision before starting a run.",
    detail: readiness.native_launch_available
      ? "The gateway path is blocked by current evidence. This is separate from the installed agent's native path."
      : "Current ACP or activation evidence does not permit a launch.",
    title: "Gateway launch is blocked",
  };
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

function AgentGlyph({
  iconRef,
  label,
}: {
  iconRef?: string | null;
  label: string;
}) {
  const [iconFailed, setIconFailed] = useState(false);
  const initials = label
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toLocaleUpperCase())
    .join("") || "A";
  return (
    <span aria-hidden="true" className="agent-glyph">
      {iconRef && !iconFailed ? (
        <img
          alt=""
          loading="lazy"
          onError={() => setIconFailed(true)}
          referrerPolicy="no-referrer"
          src={iconRef}
        />
      ) : initials}
    </span>
  );
}
