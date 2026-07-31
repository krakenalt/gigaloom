import type {
  ActionInboxCommand,
  ActionInboxItem,
  ActionInboxResponsePayload,
} from "../../api";

const encoder = new TextEncoder();

export function flattenActionInboxPages(
  pages: readonly { items: readonly ActionInboxItem[] }[],
): ActionInboxItem[] {
  const byIdentity = new Map<string, ActionInboxItem>();
  for (const page of pages) {
    for (const item of page.items) {
      byIdentity.set(`${item.authority}:${item.item_id}`, item);
    }
  }
  return [...byIdentity.values()];
}

export function actionNeedsAnswer(action: ActionInboxCommand): boolean {
  return action === "answer";
}

export function actionIsDangerous(action: ActionInboxCommand): boolean {
  return action === "cancel" || action === "deny";
}

export async function buildActionInboxResponse(
  item: ActionInboxItem,
  action: ActionInboxCommand,
  answer: string,
): Promise<ActionInboxResponsePayload> {
  if (!item.allowed_actions.includes(action)) {
    throw new Error("Action is not allowed by the owner projection.");
  }
  const normalizedAnswer = answer.trim();
  if (actionNeedsAnswer(action) && normalizedAnswer === "") {
    throw new Error("Answer is required.");
  }
  const response = actionNeedsAnswer(action)
    ? { answer: normalizedAnswer }
    : {};
  const idempotencyBody = JSON.stringify({
    action,
    expected_item_sha256: item.item_sha256,
    expected_revision: item.revision,
    response,
  });
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    encoder.encode(idempotencyBody),
  );
  const hex = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return {
    workspace_id: item.workspace_id,
    expected_revision: item.revision,
    expected_item_sha256: item.item_sha256,
    action,
    idempotency_key: `web-${hex.slice(0, 40)}`,
    response,
  };
}

export function humanizeAction(value: string): string {
  return value.replaceAll("_", " ");
}
