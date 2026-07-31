import type {
  EvidenceOmission,
  EvidenceReference,
  EvidenceSection,
  EvidenceWorkspace,
} from "../../api";

export type EvidenceWorkspaceTab =
  | "summary"
  | "evidence"
  | "context"
  | "impact"
  | "costs"
  | "trust_flows"
  | "terminal";

export const evidenceWorkspaceTabs: readonly EvidenceWorkspaceTab[] = [
  "summary",
  "evidence",
  "context",
  "impact",
  "costs",
  "trust_flows",
  "terminal",
];

const evidenceSections: readonly EvidenceSection[] = [
  "candidate",
  "gate",
  "findings",
  "change_set",
];

export function sectionsForEvidenceTab(
  tab: EvidenceWorkspaceTab,
): readonly EvidenceSection[] {
  if (tab === "summary") return [];
  if (tab === "evidence") return evidenceSections;
  return [tab];
}

export interface EvidenceSectionProjection {
  section: EvidenceSection;
  references: EvidenceReference[];
  omission: EvidenceOmission | null;
}

export function projectEvidenceSection(
  workspace: EvidenceWorkspace,
  section: EvidenceSection,
): EvidenceSectionProjection {
  return {
    section,
    references: workspace.references.filter((item) => item.section === section),
    omission:
      workspace.omissions.find((item) => item.section === section) ?? null,
  };
}

export function evidenceCoverage(workspace: EvidenceWorkspace): {
  available: number;
  omitted: number;
  stale: number;
} {
  return {
    available: workspace.references.length + (workspace.change_set === null ? 0 : 1),
    omitted: workspace.omissions.length,
    stale: workspace.staleness.stale_reference_count,
  };
}

export function humanizeEvidenceToken(value: string): string {
  return value.replaceAll("_", " ");
}
