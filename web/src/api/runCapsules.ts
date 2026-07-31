export type CapsuleFindingStatus =
  | "matched"
  | "drifted"
  | "unverifiable"
  | "omitted";

export interface CapsuleDriftFinding {
  field: string;
  status: CapsuleFindingStatus;
  expected: string | number | boolean | null;
  observed: string | number | boolean | null;
}

export interface RunCapsuleWebEvidence {
  schema_version: 1;
  kind: "gigaloom.run_capsule.web_evidence.v1";
  run_id: string;
  capsule_id: string;
  capsule_sha256: string;
  archive_sha256: string;
  created_at: string;
  content_free: true;
  integrity_status: "verified";
  correctness_claimed: false;
  signature: {
    status: "unsigned" | "signed";
    valid: boolean | null;
    signer_id: string | null;
    trust_status: string | null;
  };
  drift: {
    status: "current" | "drifted" | "unverifiable";
    matched_count: number;
    drifted_count: number;
    unverifiable_count: number;
    omitted_count: number;
    findings: CapsuleDriftFinding[];
  };
  export_path: string;
}

export interface RunCapsuleWebEvidenceResponse {
  capsule: RunCapsuleWebEvidence;
}
