import type { IntegrationFlowInventory } from "./remaining-request-graph";

export interface SkillMention {
  description: string;
  id: string;
  kind: "plugin" | "skill";
  label: string;
  mention: string;
  nativeName: string;
  source: string;
  targetIds: string[];
}

export function skillMentionOptions(
  inventory: IntegrationFlowInventory | undefined,
  harnessId: string,
  query: string,
): SkillMention[] {
  if (!inventory) return [];
  const targetPrefix = harnessId.startsWith("codex")
    ? "codex-"
    : harnessId.startsWith("claude")
      ? "claude-"
      : harnessId.startsWith("gemini")
        ? "gemini-"
        : "";
  const normalized = query.trim().toLocaleLowerCase();
  const candidates: SkillMention[] = [];
  for (const skill of inventory.root_skills ?? []) {
    candidates.push({
      description: skill.description,
      id: skill.id,
      kind: "skill",
      label: skill.name,
      mention: `@${skill.name}`,
      nativeName: skill.name,
      source: sourceLabel(skill.origin),
      targetIds: skill.target_ids,
    });
  }
  for (const plugin of inventory.root_plugins ?? []) {
    const nativeName = plugin.bundled_skills.find(
      (skillName) => skillName.toLocaleLowerCase() === plugin.name.toLocaleLowerCase(),
    ) ?? plugin.bundled_skills[0] ?? plugin.name;
    candidates.push({
      description: plugin.description,
      id: plugin.id,
      kind: "plugin",
      label: plugin.title,
      mention: plugin.invocation,
      nativeName,
      source: plugin.source_label,
      targetIds: plugin.target_ids,
    });
  }
  const deduplicated = new Map<string, SkillMention>();
  for (const candidate of candidates) {
    if (
      targetPrefix
      && !candidate.targetIds.some((target) => target.startsWith(targetPrefix))
    ) {
      continue;
    }
    if (
      normalized
      && !`${candidate.label} ${candidate.mention} ${candidate.source}`
        .toLocaleLowerCase()
        .includes(normalized)
    ) {
      continue;
    }
    deduplicated.set(candidate.nativeName, candidate);
  }
  return [...deduplicated.values()]
    .sort((left, right) => left.label.localeCompare(right.label))
    .slice(0, 24);
}

export function promptWithSkillMentions(
  prompt: string,
  skills: SkillMention[],
  harnessId: string,
): string {
  if (skills.length === 0) return prompt.trim();
  const unique = [...new Set(skills.map((skill) => skill.nativeName))];
  const invocations = harnessId.startsWith("codex")
    ? unique.map((name) => `$${name}`).join(" ")
    : unique.map((name) => `Use the ${name} skill.`).join(" ");
  return `${invocations}\n\n${prompt.trim()}`.trim();
}

function sourceLabel(origin: string) {
  if (origin === "codex-root") return "Codex";
  if (origin === "claude-root") return "Claude";
  if (origin === "gemini-root") return "Gemini";
  return "Shared";
}
