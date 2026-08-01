export const cockpitBasePath = "/web" as const;

export const primarySurfaces = [
  { id: "work", label: "Workbench", messageKey: "workbench", path: "/web/work" },
  { id: "runs", label: "Runs", messageKey: "runs", path: "/web/runs" },
  { id: "projects", label: "Projects", messageKey: "projects", path: "/web/projects" },
  { id: "coding-agents", label: "Coding Agents", messageKey: "codingAgents", path: "/web/coding-agents" },
  { id: "automation", label: "Automation", messageKey: "automationNav", path: "/web/automation" },
  { id: "evaluation", label: "Evaluation", messageKey: "evaluation", path: "/web/evaluation" },
  { id: "integrations", label: "Plugins", messageKey: "plugins", path: "/web/plugins" },
] as const;

export type SurfaceId = (typeof primarySurfaces)[number]["id"];

export function surfaceForPath(pathname: string): SurfaceId | "settings" | null {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === `${cockpitBasePath}/settings`) {
    return "settings";
  }
  const match = primarySurfaces.find(
    (surface) =>
      normalized === surface.path || normalized.startsWith(`${surface.path}/`),
  );
  if (normalized === `${cockpitBasePath}/integrations` || normalized.startsWith(`${cockpitBasePath}/integrations/`)) {
    return "integrations";
  }
  return match?.id ?? null;
}
