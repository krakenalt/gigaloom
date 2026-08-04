export const cockpitBasePath = "/web" as const;

export const primarySurfaces = [
  { id: "work", label: "Work", messageKey: "workbench", path: "/web/work" },
  { id: "inbox", label: "Inbox", messageKey: "inboxNav", path: "/web/inbox" },
  { id: "automations", label: "Automations", messageKey: "automationNav", path: "/web/automations" },
  { id: "library", label: "Library", messageKey: "libraryNav", path: "/web/library" },
  { id: "more", label: "More", messageKey: "moreNav", path: "/web/more" },
] as const;

export type SurfaceId = (typeof primarySurfaces)[number]["id"];

export function surfaceForPath(pathname: string): SurfaceId | "settings" | null {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === `${cockpitBasePath}/settings`) {
    return "settings";
  }
  if (
    normalized.startsWith(`${cockpitBasePath}/runs`)
    || normalized.startsWith(`${cockpitBasePath}/projects`)
    || normalized.startsWith(`${cockpitBasePath}/coding-agents`)
    || normalized.startsWith(`${cockpitBasePath}/evaluation`)
    || normalized.startsWith(`${cockpitBasePath}/plugins`)
    || normalized.startsWith(`${cockpitBasePath}/integrations`)
  ) {
    return "more";
  }
  if (normalized.startsWith(`${cockpitBasePath}/automation`)) {
    return "automations";
  }
  const match = primarySurfaces.find(
    (surface) =>
      normalized === surface.path || normalized.startsWith(`${surface.path}/`),
  );
  return match?.id ?? null;
}
