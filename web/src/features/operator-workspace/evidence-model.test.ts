import { describe, expect, it } from "vitest";

import type { EvidenceWorkspace } from "../../api";
import {
  evidenceCoverage,
  projectEvidenceSection,
  sectionsForEvidenceTab,
} from "./evidence-model";

const workspace: EvidenceWorkspace = {
  schema_version: 1,
  kind: "gigaloom.operator_evidence_workspace.v1",
  run: {
    run_id: "run-1",
    session_id: "session-1",
    owner_id: "operator",
    workspace_id: "workspace-1",
    status: "succeeded",
    revision: "revision-1",
  },
  references: [
    {
      section: "context",
      kind: "context_manifest",
      authority: "projects.context",
      resource_id: "context-1",
      owner_id: "operator",
      workspace_id: "workspace-1",
      revision: "context-revision-1",
      sha256: "a".repeat(64),
      state: "ready",
      freshness: "current",
    },
  ],
  change_set: {
    authority: "projects.change_set",
    owner_id: "operator",
    workspace_id: "workspace-1",
    revision: "change-revision-1",
    base_sha256: "b".repeat(64),
    patch_sha256: "c".repeat(64),
    changed_files: ["src/gigaloom/example.py"],
    truncated: false,
    freshness: "stale",
  },
  omissions: [
    {
      section: "terminal",
      reason: "not_recorded",
      authority: "native.terminal",
    },
  ],
  staleness: {
    has_stale_evidence: true,
    stale_reference_count: 1,
    sections: ["change_set"],
  },
  next_actions: [],
  projection_sha256: "d".repeat(64),
};

describe("operator evidence workspace model", () => {
  it("keeps the seven visible tabs mapped to the bounded owner sections", () => {
    expect(sectionsForEvidenceTab("evidence")).toEqual([
      "candidate",
      "gate",
      "findings",
      "change_set",
    ]);
    expect(sectionsForEvidenceTab("terminal")).toEqual(["terminal"]);
    expect(sectionsForEvidenceTab("summary")).toEqual([]);
  });

  it("projects references and omissions without synthesizing missing facts", () => {
    expect(projectEvidenceSection(workspace, "context")).toMatchObject({
      references: [{ resource_id: "context-1" }],
      omission: null,
    });
    expect(projectEvidenceSection(workspace, "terminal")).toMatchObject({
      references: [],
      omission: { reason: "not_recorded" },
    });
  });

  it("counts only retained references and the explicit change set", () => {
    expect(evidenceCoverage(workspace)).toEqual({
      available: 2,
      omitted: 1,
      stale: 1,
    });
  });
});
