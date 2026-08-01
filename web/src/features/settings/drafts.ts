import { useEffect, useState } from "react";

export interface SectionDraft<T> {
  baseRevision: string;
  dirty: boolean;
  value: T;
}

export function reconcileSectionDraft<T>(
  current: SectionDraft<T> | null,
  baseRevision: string,
  value: T,
): SectionDraft<T> {
  if (current?.dirty === true) return current;
  if (current?.baseRevision === baseRevision) return current;
  return { baseRevision, dirty: false, value };
}

export function useSectionDraft<T>(
  baseRevision: string | undefined,
  value: T | undefined,
) {
  const [draft, setDraft] = useState<SectionDraft<T> | null>(null);

  useEffect(() => {
    if (baseRevision === undefined || value === undefined) return;
    setDraft((current) => reconcileSectionDraft(current, baseRevision, value));
  }, [baseRevision, value]);

  const update = (next: T) => {
    setDraft((current) => ({
      baseRevision: current?.baseRevision ?? baseRevision ?? "",
      dirty: true,
      value: next,
    }));
  };
  const accept = (nextRevision: string, next: T) => {
    setDraft({ baseRevision: nextRevision, dirty: false, value: next });
  };

  return { accept, draft, update };
}
