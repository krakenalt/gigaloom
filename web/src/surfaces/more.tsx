import { Link } from "@tanstack/react-router";

import { MoreDestination } from "../features/work-first/MoreDestination";

export function MoreSurface() {
  return (
    <MoreDestination
      description="Open specialist product areas without expanding the primary workflow."
      sections={<nav><Link to="/web/runs">Runs</Link><Link to="/web/projects">Projects</Link><Link to="/web/coding-agents">Coding Agents</Link><Link to="/web/evaluation">Evaluation</Link><Link to="/web/plugins">Plugins</Link></nav>}
      title="More"
    />
  );
}
