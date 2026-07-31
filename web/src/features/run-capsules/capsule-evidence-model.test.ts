import { describe, expect, it } from "vitest";

import type { RunCapsuleWebEvidence } from "../../api";
import {
  capsuleFindingTone,
  shortCapsuleDigest,
  summarizeCapsuleEvidence,
} from "./capsule-evidence-model";

const evidence: RunCapsuleWebEvidence = {
  schema_version: 1,
  kind: "gigaloom.run_capsule.web_evidence.v1",
  run_id: "run_fixture",
  capsule_id: "capsule_fixture",
  capsule_sha256: "a".repeat(64),
  archive_sha256: "b".repeat(64),
  created_at: "2026-07-31T00:00:00Z",
  content_free: true,
  integrity_status: "verified",
  correctness_claimed: false,
  signature: {
    status: "unsigned",
    valid: null,
    signer_id: null,
    trust_status: null,
  },
  drift: {
    status: "drifted",
    matched_count: 1,
    drifted_count: 1,
    unverifiable_count: 1,
    omitted_count: 0,
    findings: [
      {
        field: "agent_profile.sha256",
        status: "drifted",
        expected: "a".repeat(64),
        observed: "c".repeat(64),
      },
    ],
  },
  export_path: "/api/operator/runs/run_fixture/capsule/export?workspace_id=fixture",
};

describe("Run Capsule evidence model", () => {
  it("keeps unsigned integrity distinct from identity authentication", () => {
    expect(summarizeCapsuleEvidence(evidence)).toEqual({
      integrity: "verified",
      signature: "unsigned",
      drift: "drifted",
      findingCount: 1,
      correctnessClaimed: false,
    });
  });

  it("projects drift and unavailable facts with non-success tones", () => {
    expect(capsuleFindingTone("matched")).toBe("success");
    expect(capsuleFindingTone("drifted")).toBe("danger");
    expect(capsuleFindingTone("unverifiable")).toBe("warning");
    expect(shortCapsuleDigest("a".repeat(64))).toBe(`${"a".repeat(12)}…`);
    expect(shortCapsuleDigest(null)).toBe("unknown");
  });
});
