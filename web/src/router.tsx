import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from "@tanstack/react-router";

import { AppShell } from "./AppShell";
import { validateOperationalSearch } from "./operational-navigation";
import { validateWorkbenchEntrySearch } from "./session-creation";

const rootRoute = createRootRoute({ component: AppShell });

const cockpitRoute = createRoute({
  beforeLoad: () => {
    throw redirect({ to: "/web/work" });
  },
  getParentRoute: () => rootRoute,
  path: "/web",
});

const workbenchComponent = lazyRouteComponent(
  () => import("./surfaces/workbench"),
  "WorkbenchSurface",
);
const runsComponent = lazyRouteComponent(() => import("./surfaces/runs"), "RunsSurface");
const automationComponent = lazyRouteComponent(() => import("./surfaces/automation"), "AutomationSurface");
const evaluationComponent = lazyRouteComponent(() => import("./surfaces/evaluation"), "EvaluationSurface");
const integrationsComponent = lazyRouteComponent(() => import("./surfaces/integrations"), "IntegrationsSurface");
const projectsComponent = lazyRouteComponent(() => import("./surfaces/projects"), "ProjectsSurface");
const routeAdvisorComponent = lazyRouteComponent(() => import("./surfaces/projects"), "RouteAdvisorSurface");
const mcpAppComponent = lazyRouteComponent(() => import("./surfaces/projects"), "McpAppSurface");

const routes = [
  cockpitRoute,
  createRoute({
    component: workbenchComponent,
    getParentRoute: () => rootRoute,
    path: "/web/work",
    validateSearch: validateWorkbenchEntrySearch,
  }),
  createRoute({
    component: workbenchComponent,
    getParentRoute: () => rootRoute,
    path: "/web/work/$sessionId",
    validateSearch: validateWorkbenchEntrySearch,
  }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/runs", component: runsComponent }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/runs/$runId", component: runsComponent }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/projects", component: projectsComponent }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/projects/routes/$routeDecisionId", component: routeAdvisorComponent }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/projects/mcp-apps/$serverId/$resourceSha256", component: mcpAppComponent }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/automation/workflows" }); }, getParentRoute: () => rootRoute, path: "/web/automation" }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/automation/agents", component: automationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/automation/workflows", component: automationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/automation/schedules", component: automationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/evaluation/evals" }); }, getParentRoute: () => rootRoute, path: "/web/evaluation" }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/evaluation/arena", component: evaluationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/evaluation/evals", component: evaluationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/evaluation/baselines", component: evaluationComponent, validateSearch: validateOperationalSearch }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/plugins" }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/plugins/all", component: integrationsComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/plugins/mcp", component: integrationsComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/plugins/plugins", component: integrationsComponent, validateSearch: validateOperationalSearch }),
  createRoute({ getParentRoute: () => rootRoute, path: "/web/plugins/skills", component: integrationsComponent, validateSearch: validateOperationalSearch }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/integrations" }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/integrations/harnesses" }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/integrations/models" }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/mcp" }); }, getParentRoute: () => rootRoute, path: "/web/integrations/mcp" }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/integrations/add" }),
  createRoute({ beforeLoad: () => { throw redirect({ to: "/web/plugins/all" }); }, getParentRoute: () => rootRoute, path: "/web/integrations/doctor" }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/web/settings",
    component: lazyRouteComponent(
      () => import("./surfaces/settings"),
      "SettingsSurface",
    ),
  }),
];

const routeTree = rootRoute.addChildren(routes);

export const router = createRouter({
  defaultPreload: "intent",
  routeTree,
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
