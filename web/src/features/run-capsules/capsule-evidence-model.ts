import type {
  CapsuleLaneDeltaReference,
  CapsuleFindingStatus,
  RunCapsuleWebEvidence,
} from "../../api";

export interface CapsuleEvidenceSummary {
  integrity: "verified";
  signature: "unsigned" | "valid" | "invalid";
  drift: "current" | "drifted" | "unverifiable";
  findingCount: number;
  correctnessClaimed: false;
}

export interface LaneDeltaReferenceSummary {
  changedSelectors: string;
  capturedCapsules: number;
  contentFree: true;
  hiddenStatePortabilityClaimed: false;
}

export function summarizeCapsuleEvidence(
  evidence: RunCapsuleWebEvidence,
): CapsuleEvidenceSummary {
  const signature =
    evidence.signature.status === "unsigned"
      ? "unsigned"
      : evidence.signature.valid === true
        ? "valid"
        : "invalid";
  return {
    integrity: evidence.integrity_status,
    signature,
    drift: evidence.drift.status,
    findingCount: evidence.drift.findings.length,
    correctnessClaimed: false,
  };
}

export function capsuleFindingTone(status: CapsuleFindingStatus): string {
  if (status === "matched") return "success";
  if (status === "drifted") return "danger";
  return "warning";
}

export function summarizeLaneDeltaReference(
  reference: CapsuleLaneDeltaReference,
): LaneDeltaReferenceSummary {
  return {
    changedSelectors: reference.changed_selectors.join(", "),
    capturedCapsules: reference.run_capsule_references.filter(
      (item) => item.status === "captured",
    ).length,
    contentFree: true,
    hiddenStatePortabilityClaimed: false,
  };
}

export function shortCapsuleDigest(value: string | null): string {
  if (value === null) return "unknown";
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}
