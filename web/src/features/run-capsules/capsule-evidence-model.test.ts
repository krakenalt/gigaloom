import { describe, expect, it } from "vitest";

import type { RunCapsuleWebEvidence } from "../../api";
import {
  capsuleFindingTone,
  shortCapsuleDigest,
  summarizeCapsuleEvidence,
  summarizeLaneDeltaReference,
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
  references: [],
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

  it("summarizes lane references without claiming hidden-state portability", () => {
    const reference = {
      schema_version: 1 as const,
      kind: "gigaloom.lane_delta.reference.v1" as const,
      packet_id: "lane-delta-fixture",
      packet_sha256: "d".repeat(64),
      size_bytes: 512,
      source_lane_sha256: "e".repeat(64),
      destination_lane_sha256: "f".repeat(64),
      changed_selectors: ["route_id", "model_id"],
      changed_anchor_reason_codes: ["route_id_changed", "model_id_changed"],
      run_capsule_references: [
        { role: "source" as const, sha256: "a".repeat(64), status: "captured" as const },
        { role: "destination" as const, sha256: "b".repeat(64), status: "not_captured" as const },
      ],
      disclosure_mode: "packet" as const,
      content_mode: "content_free" as const,
      content_free: true as const,
      hidden_state_portability_claimed: false as const,
      omissions: ["provider_hidden_state"],
    };

    expect(summarizeLaneDeltaReference(reference)).toEqual({
      changedSelectors: "route_id, model_id",
      capturedCapsules: 1,
      contentFree: true,
      hiddenStatePortabilityClaimed: false,
    });
  });
});
