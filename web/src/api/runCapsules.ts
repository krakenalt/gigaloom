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

export interface CapsuleLaneDeltaReference {
  schema_version: 1;
  kind: "gigaloom.lane_delta.reference.v1";
  packet_id: string;
  packet_sha256: string;
  size_bytes: number;
  source_lane_sha256: string;
  destination_lane_sha256: string;
  changed_selectors: string[];
  changed_anchor_reason_codes: string[];
  run_capsule_references: Array<{
    role: "source" | "destination";
    sha256: string;
    status: "captured" | "not_captured";
  }>;
  disclosure_mode: "packet";
  content_mode: "content_free";
  content_free: true;
  hidden_state_portability_claimed: false;
  omissions: string[];
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
  references: CapsuleLaneDeltaReference[];
  export_path: string;
}

export interface RunCapsuleWebEvidenceResponse {
  capsule: RunCapsuleWebEvidence;
}
