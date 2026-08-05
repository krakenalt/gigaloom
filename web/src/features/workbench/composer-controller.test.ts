import { describe, expect, it } from "vitest";

import { composerCommand } from "./composer-controller";

describe("composer commands", () => {
  it("recognizes /compact as an exact case-insensitive chat command", () => {
    expect(composerCommand("/compact")).toBe("compact");
    expect(composerCommand("  /COMPACT  ")).toBe("compact");
    expect(composerCommand("please /compact this")).toBeNull();
    expect(composerCommand("/compact later")).toBeNull();
  });
});
