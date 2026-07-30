export interface TextProjection {
  text: string;
  byte_count: number;
  truncated: boolean;
}

export interface TokenUsageProjection {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cached_input_tokens?: number;
  reasoning_output_tokens?: number;
  tool_tokens?: number;
}

export interface ArtifactProjection {
  type: string;
  byte_count?: number | null;
  projection_url?: string;
  source?: string;
  step_id?: string;
}

export interface CursorPage<T> {
  has_more: boolean;
  next_cursor: string | null;
  snapshot_revision: string;
  byte_count: number;
  items: T[];
}

export class CockpitApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "CockpitApiError";
    this.status = status;
  }
}

export async function fetchCockpit<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });
  return parseResponse<T>(response);
}

export async function mutateCockpit<T>(
  path: string,
  body?: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<T> {
  return writeCockpit<T>(path, "POST", body, signal);
}

export async function patchCockpit<T>(
  path: string,
  body: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<T> {
  return writeCockpit<T>(path, "PATCH", body, signal);
}

export async function putCockpit<T>(
  path: string,
  body: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<T> {
  return writeCockpit<T>(path, "PUT", body, signal);
}

export async function deleteCockpit<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return writeCockpit<T>(path, "DELETE", undefined, signal);
}

async function writeCockpit<T>(
  path: string,
  method: "DELETE" | "PATCH" | "POST" | "PUT",
  body?: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      Accept: "application/json",
      "X-GigaLoom-CSRF": "1",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    method,
    signal,
  });
  return parseResponse<T>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try {
      const parsed = JSON.parse(body) as unknown;
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "detail" in parsed &&
        typeof parsed.detail === "string"
      ) {
        detail = parsed.detail;
      }
    } catch {
      // Preserve non-JSON server errors as returned.
    }
    throw new CockpitApiError(
      response.status,
      detail || `Request failed with HTTP ${response.status}.`,
    );
  }
  return (await response.json()) as T;
}

export function withQuery(
  path: string,
  values: Readonly<Record<string, string | number | boolean | null | undefined>>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}
