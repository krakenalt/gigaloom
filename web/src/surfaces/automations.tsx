import { Link } from "@tanstack/react-router";

import { AutomationsDestination } from "../features/work-first/AutomationsDestination";

export function AutomationsSurface() {
  return (
    <AutomationsDestination
      activity={<nav><Link to="/web/automation/schedules">Schedules</Link></nav>}
      authoring={<nav><Link to="/web/automation/agents">Agents</Link><Link to="/web/automation/workflows">Workflows</Link></nav>}
      description="Author governed automation and inspect its recent activity."
      title="Automations"
    />
  );
}
