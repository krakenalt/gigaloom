export interface HandoffCapsuleResponse {
  capsule: {
    capsule_id: string;
    capsule_sha256: string;
    content_free: true;
    summary: {
      artifact_count: number;
      pending_approval_count: number;
      unresolved_question_count: number;
    };
    provenance: {
      source: { harness_id: string };
      target: { harness_id: string; session_requirement: string };
    };
    environment: {
      branch: string | null;
      head: string | null;
      snapshot_sha256: string;
    };
    continuity: {
      native_session_identity_preserved: false;
      provider_session_identity_preserved: false;
      claim: "evidence_handoff_only";
    };
  };
}
