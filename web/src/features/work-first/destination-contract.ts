import type { ReactNode } from "react";

export const workFirstDestinationIds = [
  "work",
  "inbox",
  "automations",
  "library",
  "more",
] as const;

export type WorkFirstDestinationId = (typeof workFirstDestinationIds)[number];

export type DestinationFrameProps = {
  actions?: ReactNode;
  children: ReactNode;
  description: string;
  destination: WorkFirstDestinationId;
  title: string;
};
