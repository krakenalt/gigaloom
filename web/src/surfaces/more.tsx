import { Link } from "@tanstack/react-router";

import { MoreDestination } from "../features/work-first/MoreDestination";

const destinations = [
  {
    description: "Inspect execution history, outcomes, and evidence.",
    label: "Runs",
    to: "/web/runs" as const,
  },
  {
    description: "Manage project boundaries, routes, and attached sessions.",
    label: "Projects",
    to: "/web/projects" as const,
  },
  {
    description: "Choose governed agent profiles for focused coding work.",
    label: "Coding Agents",
    to: "/web/coding-agents" as const,
  },
  {
    description: "Compare results, baselines, and evaluation evidence.",
    label: "Evaluation",
    to: "/web/evaluation" as const,
  },
  {
    description: "Connect MCP servers, plugins, skills, and integrations.",
    label: "Plugins",
    to: "/web/plugins" as const,
  },
] as const;

export function MoreSurface() {
  return (
    <MoreDestination
      description="Open specialist product areas without expanding the primary workflow."
      sections={(
        <nav aria-label="More destinations" className="more-destination-grid">
          {destinations.map((destination) => (
            <Link className="more-destination-card" key={destination.to} to={destination.to}>
              <span className="section-kicker">Product area</span>
              <strong>{destination.label}</strong>
              <span>{destination.description}</span>
              <span aria-hidden="true" className="more-destination-arrow">→</span>
            </Link>
          ))}
        </nav>
      )}
      title="More"
    />
  );
}
