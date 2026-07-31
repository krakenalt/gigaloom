import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import type {
  EligibleRouteEvidence,
  RejectedRouteEvidence,
  RouteDecisionResponse,
} from "../../api/routeAdvisor";
import {
  fetchRouteDecision,
  overrideRoute,
} from "../../api/routeAdvisor";
import {
  projectRouteDecision,
  routeCostLabel,
  shortRouteDigest,
} from "./route-advisor-model";
import "./route-advisor.css";

type Locale = "en" | "ru";

const labels = {
  en: {
    automaticDisabled: "Automatic execution disabled",
    confirmationRequired: "Manual confirmation required before run",
    cost: "Cost knowledge",
    eligible: "Eligible",
    failed: "Route decision unavailable",
    override: "Select this route",
    rejected: "Rejected",
    selected: "Selected",
    title: "Governed Route Advisor",
  },
  ru: {
    automaticDisabled: "Автоматический запуск отключён",
    confirmationRequired: "Перед запуском требуется ручное подтверждение",
    cost: "Данные о стоимости",
    eligible: "Допущен",
    failed: "Route Decision недоступен",
    override: "Выбрать этот маршрут",
    rejected: "Отклонён",
    selected: "Выбран",
    title: "Governed Route Advisor",
  },
} as const;

export default function RouteAdvisorInspection({
  locale,
  routeDecisionId,
}: {
  locale: Locale;
  routeDecisionId: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    enabled: routeDecisionId !== "",
    queryFn: ({ signal }) => fetchRouteDecision(routeDecisionId, signal),
    queryKey: routeDecisionKey(routeDecisionId),
  });
  const mutation = useMutation({
    mutationFn: ({ receiptId, routeId }: { receiptId: string; routeId: string }) =>
      overrideRoute(receiptId, routeId),
    onSuccess: (response) => {
      queryClient.setQueryData(
        routeDecisionKey(response.receipt.route_decision_id),
        response,
      );
    },
  });
  const response = mutation.data ?? query.data;
  const projection = useMemo(() => {
    if (response === undefined) return null;
    try {
      return projectRouteDecision(response);
    } catch {
      return null;
    }
  }, [response]);

  if (query.isPending) {
    return <div aria-busy="true" className="route-advisor-skeleton" />;
  }
  if (query.isError || response === undefined || projection === null) {
    return (
      <section className="route-advisor-error" role="alert">
        {labels[locale].failed}
      </section>
    );
  }
  return (
    <RouteDecisionPanel
      locale={locale}
      onOverride={(routeId) =>
        mutation.mutate({
          receiptId: projection.receipt.route_decision_id,
          routeId,
        })
      }
      overridingRouteId={mutation.isPending ? mutation.variables?.routeId : null}
      response={response}
    />
  );
}

export function RouteDecisionPanel({
  locale,
  onOverride,
  overridingRouteId,
  response,
}: {
  locale: Locale;
  onOverride: (routeId: string) => void;
  overridingRouteId: string | null | undefined;
  response: RouteDecisionResponse;
}) {
  const projection = projectRouteDecision(response);
  const copy = labels[locale];
  return (
    <section className="route-advisor-inspection">
      <header>
        <div>
          <span className="section-kicker">{projection.receipt.outcome}</span>
          <h2>{copy.title}</h2>
        </div>
        <div className="route-advisor-safety" role="status">
          <strong>{copy.automaticDisabled}</strong>
          <span>{copy.confirmationRequired}</span>
        </div>
      </header>
      <div className="route-advisor-bindings">
        <code>{projection.receipt.route_decision_id}</code>
        <code>{shortRouteDigest(projection.receipt.receipt_digest)}</code>
      </div>
      <div className="route-advisor-grid">
        {projection.receipt.eligible_routes.map((route) => (
          <EligibleRouteCard
            key={route.route_id}
            locale={locale}
            onOverride={onOverride}
            overriding={overridingRouteId === route.route_id}
            route={route}
            selected={route.route_id === projection.receipt.recommended_route_id}
          />
        ))}
        {projection.receipt.rejected_routes.map((route) => (
          <RejectedRouteCard key={route.route_id} locale={locale} route={route} />
        ))}
      </div>
    </section>
  );
}

function EligibleRouteCard({
  locale,
  onOverride,
  overriding,
  route,
  selected,
}: {
  locale: Locale;
  onOverride: (routeId: string) => void;
  overriding: boolean;
  route: EligibleRouteEvidence;
  selected: boolean;
}) {
  const copy = labels[locale];
  return (
    <article className={selected ? "route-card selected" : "route-card"}>
      <header>
        <div>
          <strong>{route.route_id}</strong>
          <span>{route.agent_id}</span>
        </div>
        <span className="status-label success">
          {selected ? copy.selected : copy.eligible}
        </span>
      </header>
      <dl>
        <Metric label="Transport" value={route.transport_class} />
        <Metric label={copy.cost} value={routeCostLabel(route)} />
        <Metric label="Compatibility" value={route.compatibility_grade} />
        <Metric label="Rank" value={route.rank === null ? "—" : String(route.rank)} />
      </dl>
      {selected ? null : (
        <button
          className="secondary-button"
          disabled={overriding}
          onClick={() => onOverride(route.route_id)}
          type="button"
        >
          {copy.override}
        </button>
      )}
    </article>
  );
}

function RejectedRouteCard({
  locale,
  route,
}: {
  locale: Locale;
  route: RejectedRouteEvidence;
}) {
  return (
    <article className="route-card rejected">
      <header>
        <div>
          <strong>{route.route_id}</strong>
          <span>{route.agent_id}</span>
        </div>
        <span className="status-label danger">{labels[locale].rejected}</span>
      </header>
      <ul>
        {route.reason_codes.map((reason) => (
          <li key={reason}>{reason.replaceAll("_", " ")}</li>
        ))}
      </ul>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function routeDecisionKey(routeDecisionId: string) {
  return ["cockpit", "route-decision", routeDecisionId] as const;
}
